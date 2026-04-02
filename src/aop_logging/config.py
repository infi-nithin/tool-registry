"""Configuration for AOP Logging."""

import os
from dataclasses import dataclass, field
from typing import List

from aop_logging.models import LogLevel


@dataclass
class LoggingConfig:
    """Configuration for AOP logging."""
    
    # Log level
    log_level: LogLevel = field(default=LogLevel.INFO)
    
    # Middleware settings
    exclude_paths: List[str] = field(default_factory=lambda: [
        "/docs", "/openapi.json", "/redoc", "/static"
    ])
    exclude_methods: List[str] = field(default_factory=lambda: ["OPTIONS"])
    
    # Output settings
    enable_console_output: bool = True
    enable_file_output: bool = True
    log_dir: str = field(default_factory=lambda: os.path.join(os.getcwd(), "logs"))
    log_file_prefix: str = "app"
    log_rotation_when: str = "H"  # Rotate every hour
    log_rotation_interval: int = 1  # Every 1 hour
    log_backup_count: int = 168  # Keep 1 week of hourly logs (24 * 7)
    
    # MCP logging
    log_mcp_tool_calls: bool = True
    log_mcp_server_ops: bool = True
    
    # Performance thresholds (in milliseconds)
    slow_operation_threshold_ms: float = 1000.0
    warning_threshold_ms: float = 500.0
    
    # Sanitization
    sensitive_headers: List[str] = field(default_factory=lambda: [
        "authorization", "x-api-key", "cookie", "set-cookie"
    ])
    sensitive_params: List[str] = field(default_factory=lambda: [
        "password", "token", "api_key", "secret", "access_token", "refresh_token"
    ])
    
    @classmethod
    def from_env(cls) -> "LoggingConfig":
        """Create configuration from environment variables."""
        log_level_str = os.getenv("AOP_LOG_LEVEL", "INFO").upper()
        try:
            log_level = LogLevel(log_level_str)
        except ValueError:
            log_level = LogLevel.INFO
        
        return cls(
            log_level=log_level,
            enable_file_output=os.getenv("AOP_ENABLE_FILE_LOG", "true").lower() == "true",
            log_dir=os.getenv("AOP_LOG_DIR", os.path.join(os.getcwd(), "logs")),
            log_file_prefix=os.getenv("AOP_LOG_FILE_PREFIX", "app"),
            log_backup_count=int(os.getenv("AOP_LOG_BACKUP_COUNT", "168")),
            log_mcp_tool_calls=os.getenv("AOP_LOG_MCP_TOOLS", "true").lower() == "true",
            log_mcp_server_ops=os.getenv("AOP_LOG_MCP_SERVERS", "true").lower() == "true",
            slow_operation_threshold_ms=float(os.getenv("AOP_SLOW_THRESHOLD_MS", "1000")),
            warning_threshold_ms=float(os.getenv("AOP_WARNING_THRESHOLD_MS", "500")),
        )


# Default configuration instance
default_config = LoggingConfig()
