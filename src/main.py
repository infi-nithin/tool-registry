from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.v1 import endpoints
from service.mcp_service import get_registry, initialize_main_server
from aop_logging import get_aop_logger, patch_fastmcp_server
import asyncio
import sys
import logging
from aop_logging import AOPLoggingMiddleware, RequestTimingMiddleware

# Configure root logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
)


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    mcp_context = None
    mcp_http_app = None
    registry = None
    logger = get_aop_logger().logger

    try:
        # Step 1: Initialize main server
        logger.info("Initializing main MCP server...")
        logger.info("Current logger handlers before MCP initialization:")
        main_server = await initialize_main_server(name="main-mcp-server")
        logger.disabled = False
        main_server = patch_fastmcp_server(main_server)
        logger.info("MCP server patched with AOP logging")
        logger.info(f"Main MCP server initialized: {main_server}")
        # Step 2: Get the registry
        registry = await get_registry()

        # Step 3: Create and mount the MCP HTTP app
        try:
            mcp_http_app = main_server.http_app(path="/", transport="streamable-http")
            # Enter MCP lifespan to initialize the task group
            mcp_context = mcp_http_app.lifespan(mcp_http_app)
            await mcp_context.__aenter__()
            # Mount the MCP app now that lifespan is active
            app.mount("/mcp", mcp_http_app)
            logger.info("MCP HTTP endpoint mounted at /mcp")
        except Exception as e:
            logger.warning(f"Warning: Could not setup MCP HTTP endpoint: {e}")

        yield
    finally:
        if mcp_context:
            try:
                await mcp_context.__aexit__(None, None, None)
                logger.info("MCP lifespan exited successfully")
            except Exception as e:
                logger.error(f"Error during MCP lifespan exit: {e}")


def create_application() -> FastAPI:
    app = FastAPI(
        title="Tool Registry",
        description="Dynamic MCP Server Management API",
        version="0.1.0",
        lifespan=combined_lifespan,
    )

    app.add_middleware(AOPLoggingMiddleware)
    app.add_middleware(RequestTimingMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(endpoints.router, prefix="/api/v1")

    @app.get("/", tags=["info"])
    async def root():
        return {
            "service": "Tool Registry",
            "version": "0.1.0",
            "description": "Dynamic MCP Server Management",
            "endpoints": {
                "docs": "/docs",
                "api": "/api/v1",
                "mcp_http": "/mcp",
                "status": "/api/v1/mcp/status",
            },
        }

    return app


# Module-level app export for uvicorn (e.g., uvicorn main:app --reload)
app = create_application()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
