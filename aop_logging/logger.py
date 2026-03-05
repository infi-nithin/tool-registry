"""AOP Logging infrastructure with decorators and async support."""

import functools
import inspect
import logging
import os
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Callable, Dict, Optional, TypeVar

from aop_logging.models import (
    LogEntry,
    APILogEntry,
    MCPToolLogEntry,
    ServiceMethodLogEntry,
    OperationStatus,
    OperationType,
    LogLevel,
)
from aop_logging.config import default_config

# Context variable for correlation ID tracking
correlation_id_var: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)

# Type variable for generic function types
F = TypeVar("F", bound=Callable[..., Any])


class AOPLogger:
    """Aspect-Oriented Programming Logger for comprehensive operation tracking."""
    
    def __init__(
        self,
        name: str = "aop_logger",
        log_level: LogLevel = LogLevel.INFO,
        config = None
    ):
        self.config = config or default_config
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, log_level.value))
        
        # Prevent duplicate handlers
        if not self.logger.handlers:
            formatter = logging.Formatter(
                "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
            )
            
            # Console handler
            if self.config.enable_console_output:
                console_handler = logging.StreamHandler()
                console_handler.setFormatter(formatter)
                self.logger.addHandler(console_handler)
            
            # File handler with hourly rotation
            if self.config.enable_file_output:
                # Create logs directory if it doesn't exist
                os.makedirs(self.config.log_dir, exist_ok=True)
                
                log_file_path = os.path.join(
                    self.config.log_dir,
                    f"{self.config.log_file_prefix}.log"
                )
                
                file_handler = TimedRotatingFileHandler(
                    filename=log_file_path,
                    when=self.config.log_rotation_when,  # 'H' for hourly
                    interval=self.config.log_rotation_interval,  # 1 hour
                    backupCount=self.config.log_backup_count,  # Keep 1 week of logs
                    encoding="utf-8"
                )
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)
        
        self._operation_stats: Dict[str, Dict[str, Any]] = {}
    
    def set_correlation_id(self, correlation_id: Optional[str] = None) -> str:
        """Set or generate a correlation ID for request tracing."""
        if correlation_id is None:
            correlation_id = str(uuid.uuid4())
        correlation_id_var.set(correlation_id)
        return correlation_id
    
    def get_correlation_id(self) -> Optional[str]:
        """Get the current correlation ID."""
        return correlation_id_var.get()
    
    def clear_correlation_id(self) -> None:
        """Clear the current correlation ID."""
        correlation_id_var.set(None)
    
    def _sanitize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize sensitive data from logs."""
        if not data:
            return {}
        
        sensitive_keys = {"password", "token", "api_key", "secret", "authorization", "auth"}
        sanitized = {}
        
        for key, value in data.items():
            if any(sensitive in key.lower() for sensitive in sensitive_keys):
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_data(value)
            else:
                sanitized[key] = value
        
        return sanitized
    
    def _log_entry(self, entry: LogEntry) -> None:
        """Log a structured log entry."""
        log_data = entry.model_dump(exclude_none=True)
        
        # Format the log message based on operation type
        if entry.status == OperationStatus.FAILURE:
            self.logger.error(f"[{entry.operation_type}] {entry.operation_name} FAILED | Duration: {entry.duration_ms}ms | Error: {entry.error_message}", extra={"log_data": log_data})
        elif entry.duration_ms and entry.duration_ms > 1000:  # Slow operation warning
            self.logger.warning(f"[{entry.operation_type}] {entry.operation_name} SUCCESS | Duration: {entry.duration_ms}ms (SLOW)", extra={"log_data": log_data})
        else:
            self.logger.info(f"[{entry.operation_type}] {entry.operation_name} {entry.status.value.upper()} | Duration: {entry.duration_ms}ms", extra={"log_data": log_data})
    
    def log_api_call(
        self,
        http_method: str,
        endpoint_path: str,
        status_code: Optional[int] = None,
        duration_ms: Optional[float] = None,
        request_data: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
        query_params: Optional[Dict[str, Any]] = None,
        path_params: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        """Log an API endpoint call."""
        status = OperationStatus.FAILURE if error else (OperationStatus.SUCCESS if status_code and status_code < 400 else OperationStatus.FAILURE)
        
        entry = APILogEntry(
            timestamp=datetime.now(datetime.timezone.utc),
            operation_type=OperationType.API_ENDPOINT,
            operation_name=f"{http_method} {endpoint_path}",
            status=status,
            duration_ms=duration_ms,
            http_method=http_method,
            endpoint_path=endpoint_path,
            status_code=status_code,
            request_data=self._sanitize_data(request_data or {}),
            response_data=self._sanitize_data(response_data or {}),
            query_params=query_params,
            path_params=path_params,
            correlation_id=self.get_correlation_id(),
            client_ip=client_ip,
            user_agent=user_agent,
            error_message=str(error) if error else None,
            error_type=type(error).__name__ if error else None,
        )
        
        self._log_entry(entry)
    
    def log_mcp_tool_call(
        self,
        tool_name: str,
        server_name: str,
        duration_ms: Optional[float] = None,
        arguments: Optional[Dict[str, Any]] = None,
        result_summary: Optional[str] = None,
        error: Optional[Exception] = None,
        tags: Optional[list[str]] = None,
    ) -> None:
        """Log an MCP tool invocation."""
        status = OperationStatus.FAILURE if error else OperationStatus.SUCCESS
        
        entry = MCPToolLogEntry(
            timestamp=datetime.now(datetime.timezone.utc),
            operation_type=OperationType.MCP_TOOL,
            operation_name=tool_name,
            status=status,
            duration_ms=duration_ms,
            tool_name=tool_name,
            server_name=server_name,
            arguments=self._sanitize_data(arguments or {}),
            result_summary=result_summary,
            source_server=server_name,
            tags=tags,
            correlation_id=self.get_correlation_id(),
            error_message=str(error) if error else None,
            error_type=type(error).__name__ if error else None,
        )
        
        self._log_entry(entry)
    
    def log_service_method(
        self,
        service_name: str,
        method_name: str,
        duration_ms: Optional[float] = None,
        request_data: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
        class_name: Optional[str] = None,
        module_name: Optional[str] = None,
    ) -> None:
        """Log a service method call."""
        status = OperationStatus.FAILURE if error else OperationStatus.SUCCESS
        
        entry = ServiceMethodLogEntry(
            timestamp=datetime.now(datetime.timezone.utc),
            operation_type=OperationType.SERVICE_METHOD,
            operation_name=f"{service_name}.{method_name}",
            status=status,
            duration_ms=duration_ms,
            service_name=service_name,
            method_name=method_name,
            class_name=class_name,
            module_name=module_name,
            request_data=self._sanitize_data(request_data or {}),
            response_data=self._sanitize_data(response_data or {}),
            correlation_id=self.get_correlation_id(),
            error_message=str(error) if error else None,
            error_type=type(error).__name__ if error else None,
        )
        
        self._log_entry(entry)


# Global logger instance
_aop_logger: Optional[AOPLogger] = None


def get_aop_logger() -> AOPLogger:
    """Get or create the global AOP logger instance."""
    global _aop_logger
    if _aop_logger is None:
        _aop_logger = AOPLogger()
    return _aop_logger


def set_aop_logger(logger: AOPLogger) -> None:
    """Set the global AOP logger instance."""
    global _aop_logger
    _aop_logger = logger


def log_method(service_name: Optional[str] = None) -> Callable[[F], F]:
    """Decorator to log service method calls with timing."""
    def decorator(func: F) -> F:
        nonlocal service_name
        if service_name is None:
            service_name = func.__qualname__.split(".")[0] if "." in func.__qualname__ else "UnknownService"
        
        method_name = func.__name__
        module_name = func.__module__
        
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                logger = get_aop_logger()
                start_time = time.perf_counter()
                error = None
                
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    error = e
                    raise
                finally:
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    
                    # Extract request data from args/kwargs (skip self/cls)
                    request_data = {}
                    if len(args) > 1:
                        request_data["args"] = str(args[1:])
                    if kwargs:
                        request_data["kwargs"] = kwargs
                    
                    logger.log_service_method(
                        service_name=service_name,
                        method_name=method_name,
                        duration_ms=duration_ms,
                        request_data=request_data,
                        error=error,
                        class_name=service_name,
                        module_name=module_name,
                    )
            
            return async_wrapper  # type: ignore
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                logger = get_aop_logger()
                start_time = time.perf_counter()
                error = None
                
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    error = e
                    raise
                finally:
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    
                    request_data = {}
                    if len(args) > 1:
                        request_data["args"] = str(args[1:])
                    if kwargs:
                        request_data["kwargs"] = kwargs
                    
                    logger.log_service_method(
                        service_name=service_name,
                        method_name=method_name,
                        duration_ms=duration_ms,
                        request_data=request_data,
                        error=error,
                        class_name=service_name,
                        module_name=module_name,
                    )
            
            return sync_wrapper
    
    return decorator
