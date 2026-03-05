"""MCP Tool wrapper for AOP logging of tool invocations."""

import functools
import time
from typing import Any, Callable, Optional, TypeVar

from mcp.server.fastmcp.tools import Tool as MCPTool
from mcp.types import CallToolRequest

from aop_logging.logger import get_aop_logger, AOPLogger

F = TypeVar("F", bound=Callable[..., Any])


class MCPToolLogger:
    """Logger wrapper for MCP tools to track invocations."""
    
    def __init__(self, logger: Optional[AOPLogger] = None):
        self.logger = logger or get_aop_logger()
    
    def wrap_tool(
        self,
        tool_func: F,
        tool_name: Optional[str] = None,
        server_name: str = "main-mcp-server",
        tags: Optional[list[str]] = None,
    ) -> F:
        """Wrap an MCP tool function with logging."""
        name = tool_name or getattr(tool_func, "__name__", "unknown_tool")
        tool_tags = tags or []
        
        @functools.wraps(tool_func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.perf_counter()
            error = None
            result_summary = None
            
            # Extract arguments for logging
            arguments = {}
            if args:
                arguments["args"] = str(args)
            if kwargs:
                # Filter out request objects but keep other params
                for key, value in kwargs.items():
                    if not isinstance(value, CallToolRequest):
                        arguments[key] = value
            
            try:
                result = await tool_func(*args, **kwargs)
                
                # Create result summary
                if result:
                    if hasattr(result, "content"):
                        content_len = len(result.content) if result.content else 0
                        result_summary = f"Success: {content_len} content items"
                    else:
                        result_summary = f"Success: {str(result)[:100]}"
                else:
                    result_summary = "Success: No result"
                
                return result
            except Exception as e:
                error = e
                result_summary = f"Error: {str(e)}"
                raise
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                
                self.logger.log_mcp_tool_call(
                    tool_name=name,
                    server_name=server_name,
                    duration_ms=duration_ms,
                    arguments=arguments,
                    result_summary=result_summary,
                    error=error,
                    tags=tool_tags,
                )
        
        return async_wrapper  # type: ignore
    
    def wrap_tool_class(self, tool_class: Any, server_name: str = "main-mcp-server") -> Any:
        """Wrap all async methods of an MCP tool class with logging."""
        original_call = getattr(tool_class, "__call__", None)
        
        if original_call:
            tool_name = getattr(tool_class, "name", tool_class.__name__)
            tags = getattr(tool_class, "tags", None)
            
            wrapped_call = self.wrap_tool(
                tool_func=original_call,
                tool_name=tool_name,
                server_name=server_name,
                tags=tags,
            )
            setattr(tool_class, "__call__", wrapped_call)
        
        return tool_class


def create_logged_tool(
    server: Any,
    tool_func: F,
    tool_name: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> F:
    """Create a logged tool and register it with the MCP server."""
    logger = MCPToolLogger()
    
    # Wrap the tool function with logging
    wrapped_func = logger.wrap_tool(
        tool_func=tool_func,
        tool_name=tool_name or getattr(tool_func, "__name__", "unknown"),
        server_name=getattr(server, "name", "main-mcp-server"),
        tags=tags,
    )
    
    # Register with the server (if using FastMCP)
    if hasattr(server, "tool"):
        # Use the server's tool decorator
        decorator = server.tool(
            name=tool_name,
            description=description,
            tags=tags,
        )
        return decorator(wrapped_func)
    
    return wrapped_func


def patch_fastmcp_server(server: Any) -> Any:
    """Patch a FastMCP server to automatically log all tool calls."""
    logger = MCPToolLogger()
    
    # Store original tool registration method
    original_tool_method = getattr(server, "tool", None)
    
    if original_tool_method:
        def logged_tool(*args: Any, **kwargs: Any) -> Callable:
            """Tool decorator that wraps with logging."""
            # Extract parameters
            name = kwargs.get("name") or (args[0] if args else None)
            tags = kwargs.get("tags", [])
            server_name = getattr(server, "name", "main-mcp-server")
            
            def decorator(func: F) -> F:
                # Wrap with logging first
                wrapped = logger.wrap_tool(
                    tool_func=func,
                    tool_name=name or getattr(func, "__name__", "unknown"),
                    server_name=server_name,
                    tags=tags,
                )
                # Then register with original tool method
                return original_tool_method(*args, **kwargs)(wrapped)
            
            return decorator
        
        # Replace the tool method
        setattr(server, "tool", logged_tool)
    
    # Also patch add_tool if it exists
    original_add_tool = getattr(server, "add_tool", None)
    if original_add_tool:
        async def logged_add_tool(tool: MCPTool) -> None:
            """Add tool with logging wrapper."""
            server_name = getattr(server, "name", "main-mcp-server")
            
            # Wrap the tool's call method
            if hasattr(tool, "fn") and tool.fn:
                tool.fn = logger.wrap_tool(
                    tool_func=tool.fn,
                    tool_name=getattr(tool, "name", "unknown"),
                    server_name=server_name,
                    tags=getattr(tool, "tags", None),
                )
            
            return await original_add_tool(tool)
        
        setattr(server, "add_tool", logged_add_tool)
    
    return server


class MCPCallTracker:
    """Track MCP tool calls across the application."""
    
    def __init__(self):
        self.logger = get_aop_logger()
        self._call_count = 0
        self._error_count = 0
        self._total_duration_ms = 0.0
    
    def record_call(
        self,
        tool_name: str,
        server_name: str,
        duration_ms: float,
        success: bool,
        error: Optional[Exception] = None,
    ) -> None:
        """Record a tool call for statistics."""
        self._call_count += 1
        self._total_duration_ms += duration_ms
        if not success:
            self._error_count += 1
    
    def get_stats(self) -> dict:
        """Get tracking statistics."""
        return {
            "total_calls": self._call_count,
            "error_count": self._error_count,
            "success_count": self._call_count - self._error_count,
            "average_duration_ms": self._total_duration_ms / self._call_count if self._call_count > 0 else 0,
            "total_duration_ms": self._total_duration_ms,
        }


# Global tracker instance
_tracker: Optional[MCPCallTracker] = None


def get_mcp_tracker() -> MCPCallTracker:
    """Get or create the global MCP call tracker."""
    global _tracker
    if _tracker is None:
        _tracker = MCPCallTracker()
    return _tracker
