"""FastAPI middleware for AOP logging of API requests."""

import time
import json
from typing import Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from aop_logging.logger import get_aop_logger


class AOPLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all API requests safely (does NOT break streaming / SSE)."""

    def __init__(
        self,
        app: ASGIApp,
        exclude_paths: Optional[list[str]] = None,
        exclude_methods: Optional[list[str]] = None,
        max_body_log_size: int = 10000,
    ):
        super().__init__(app)
        self.logger = get_aop_logger()
        self.exclude_paths = exclude_paths or [
            "/docs",
            "/openapi.json",
            "/redoc",
            "/mcp",
        ]
        self.exclude_methods = exclude_methods or ["OPTIONS"]
        self.max_body_log_size = max_body_log_size

    def _should_log(self, request: Request) -> bool:
        """Check if the request should be logged."""
        if request.method in self.exclude_methods:
            return False

        path = request.url.path

        for exclude_path in self.exclude_paths:
            if path.startswith(exclude_path):
                return False

        return True

    async def dispatch(self, request: Request, call_next: Callable) -> Response:

        if not self._should_log(request):
            return await call_next(request)

        correlation_id = request.headers.get("X-Correlation-ID")
        self.logger.set_correlation_id(correlation_id)

        start_time = time.perf_counter()
        error = None
        response: Optional[Response] = None

        http_method = request.method
        endpoint_path = request.url.path
        query_params = dict(request.query_params)
        path_params = dict(request.path_params)
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        request_data = None

        # ⭐ SAFE BODY LOGGING (no _receive override)
        if http_method in ["POST", "PUT", "PATCH"]:
            content_type = request.headers.get("content-type", "")

            if "application/json" in content_type:
                try:
                    body = await request.body()   # FastAPI caches internally → safe
                    if body and len(body) < self.max_body_log_size:
                        try:
                            request_data = json.loads(body)
                        except json.JSONDecodeError:
                            request_data = {
                                "raw_body": body.decode("utf-8", errors="ignore")[:1000]
                            }
                except Exception:
                    pass

        try:
            response = await call_next(request)
            return response

        except Exception as e:
            error = e
            raise

        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            status_code = response.status_code if response else 500

            self.logger.log_api_call(
                http_method=http_method,
                endpoint_path=endpoint_path,
                status_code=status_code,
                duration_ms=duration_ms,
                request_data=request_data,
                query_params=query_params or None,
                path_params=path_params or None,
                error=error,
                client_ip=client_ip,
                user_agent=user_agent,
            )

            self.logger.clear_correlation_id()



class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Lightweight middleware for adding timing headers to responses."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        response.headers["X-Response-Time-Ms"] = str(round(duration_ms, 2))
        response.headers["X-Correlation-ID"] = get_aop_logger().get_correlation_id() or ""
        
        return response
