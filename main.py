import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1 import endpoints
from db.startup import initialize_database, restore_mcp_servers, close_database
from db.connection import get_db_session
from aop_logging import (
    AOPLoggingMiddleware,
    RequestTimingMiddleware,
    get_aop_logger,
    patch_fastmcp_server,
)

# Configure root logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
    ]
)


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    """Application lifespan manager with database initialization.
    
    Handles database startup initialization, MCP server restoration,
    and cleanup on shutdown.
    """
    mcp_context = None
    mcp_http_app = None
    logger = get_aop_logger()
    registry = None
    
    try:
        # Step 1: Initialize database
        logger.logger.info("Initializing database...")
        db_initialized = await initialize_database()
        if not db_initialized:
            logger.logger.error("Database initialization failed - application may not function correctly")
        else:
            logger.logger.info("Database initialized successfully")
        
        # Step 2: Restore MCP servers from database
        logger.logger.info("Restoring MCP servers from database...")
        restored_count, errors = await restore_mcp_servers(server_name="main-mcp-server")
        logger.logger.info(f"Restored {restored_count} MCP servers from database")
        if errors:
            for error in errors:
                logger.logger.warning(f"Server restoration error: {error}")
        
        # Step 3: Get the registry and main server for HTTP app mounting
        from service.mcp_registry_factory import get_registry
        from db.connection import async_session_factory
        
        if async_session_factory:
            async with async_session_factory() as session:
                registry = await get_registry(session)
                main_server = await registry.get_main_server()
                
                if main_server:
                    logger.logger.info(f"Main MCP server initialized: {main_server.name}")
                    
                    # Patch the MCP server for AOP logging
                    main_server = patch_fastmcp_server(main_server)
                    logger.logger.info("MCP server patched with AOP logging")
                    
                    # Create and mount the MCP HTTP app
                    try:
                        mcp_http_app = main_server.http_app(path="/", transport="streamable-http")
                        # Enter MCP lifespan to initialize the task group
                        mcp_context = mcp_http_app.lifespan(mcp_http_app)
                        await mcp_context.__aenter__()
                        # Mount the MCP app now that lifespan is active
                        app.mount("/mcp", mcp_http_app)
                        logger.logger.info("MCP HTTP endpoint mounted at /mcp")
                    except Exception as e:
                        logger.logger.error(f"Warning: Could not setup MCP HTTP endpoint: {e}")
                        import traceback
                        traceback.print_exc()
        
        yield
        
    finally:
        # Exit MCP lifespan if it was entered
        if mcp_context:
            try:
                await mcp_context.__aexit__(None, None, None)
                logger.logger.info("MCP lifespan exited successfully")
            except Exception as e:
                logger.logger.error(f"Error during MCP lifespan exit: {e}")
        
        # Cleanup database resources
        logger.logger.info("Closing database connections...")
        await close_database()
        logger.logger.info("Database connections closed")


def create_application() -> FastAPI:
    app = FastAPI(
        title="Tool Registry",
        description="Dynamic MCP Server Management API",
        version="0.1.0",
        lifespan=combined_lifespan,
    )

    # Add AOP logging middleware (must be first to capture all requests)
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
    uvicorn.run(app, host="0.0.0.0", port=8000)
