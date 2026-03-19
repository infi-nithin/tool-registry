import time
import functools
import logging
from typing import Any, Callable, Optional, TypeVar
from aop_logging.logger import get_aop_logger, AOPLogger
import inspect


# Type variable for generic function types
F = TypeVar("F", bound=Callable[..., Any])

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
