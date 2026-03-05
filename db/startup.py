"""Database startup initialization module.

This module handles all database initialization on application startup,
including running migrations, verifying connections, and restoring
MCP servers from the database.

Example:
    >>> from db.startup import initialize_database, restore_mcp_servers, close_database
    >>> 
    >>> # Initialize database on startup
    >>> await initialize_database()
    >>> 
    >>> # Restore MCP servers from database
    >>> await restore_mcp_servers()
    >>> 
    >>> # Cleanup on shutdown
    >>> await close_database()
"""

import logging
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from db.migrations import run_migrations
from db.connection import (
    init_engine,
    init_session_factory,
    health_check,
    close_connection,
)
from service.mcp_registry_factory import (
    initialize_registry_with_main_server,
    get_factory,
)

logger = logging.getLogger(__name__)

# Global registry reference for cleanup
_registry_instance: Optional[object] = None


async def initialize_database() -> bool:
    """Initialize the database on application startup.
    
    Performs the following initialization steps:
    1. Runs Alembic migrations to ensure schema is up to date
    2. Initializes the database engine and session factory
    3. Verifies database connectivity
    4. Logs initialization status
    
    Returns:
        True if initialization was successful, False otherwise
        
    Example:
        >>> success = await initialize_database()
        >>> if success:
        ...     print("Database initialized successfully")
        ... else:
        ...     print("Database initialization failed")
    """
    logger.info("Starting database initialization...")
    
    try:
        # Step 1: Run Alembic migrations
        logger.info("Running database migrations...")
        await run_migrations(target="head")
        logger.info("Database migrations completed successfully")
        
        # Step 2: Initialize database engine
        logger.info("Initializing database engine...")
        await init_engine()
        logger.info("Database engine initialized")
        
        # Step 3: Initialize session factory
        logger.info("Initializing session factory...")
        await init_session_factory()
        logger.info("Session factory initialized")
        
        # Step 4: Verify database connectivity
        logger.info("Verifying database connection...")
        is_healthy = await health_check()
        
        if not is_healthy:
            logger.error("Database health check failed")
            return False
        
        logger.info("Database connection verified successfully")
        logger.info("Database initialization completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def restore_mcp_servers(
    server_name: str = "main-mcp-server"
) -> Tuple[int, list]:
    """Restore MCP servers from the database on startup.
    
    Uses the MCPRegistryFactory to initialize the registry with the main
    server and restore all ACTIVE servers from the database to FastMCP.
    
    Args:
        server_name: Name for the main MCP server. Defaults to "main-mcp-server".
        
    Returns:
        Tuple of (restored_count: int, errors: list)
        - restored_count: Number of servers successfully restored
        - errors: List of error messages for failed restorations
        
    Example:
        >>> restored, errors = await restore_mcp_servers()
        >>> print(f"Restored {restored} servers")
        >>> if errors:
        ...     for error in errors:
        ...         print(f"Error: {error}")
    """
    logger.info("Starting MCP server restoration from database...")
    
    # Import here to avoid circular imports
    from db.connection import async_session_factory
    
    if async_session_factory is None:
        logger.error("Database session factory not initialized")
        return 0, ["Database session factory not initialized"]
    
    restored_count = 0
    errors = []
    
    try:
        # Create a session for restoration
        async with async_session_factory() as session:
            # Initialize registry with main server and restore all active servers
            registry = await initialize_registry_with_main_server(
                session=session,
                server_name=server_name,
                restore_servers=True,
            )
            
            # Store registry reference for later cleanup
            global _registry_instance
            _registry_instance = registry
            
            # Get restoration results from the registry
            # The initialize_registry_with_main_server function handles restoration
            # and logs any errors, but we can query the registry for status
            servers = await registry.list_servers()
            active_servers = [s for s in servers if s.status.value == "active"]
            
            restored_count = len(active_servers)
            logger.info(f"MCP server restoration completed: {restored_count} servers restored")
            
    except Exception as e:
        error_msg = f"Failed to restore MCP servers: {str(e)}"
        logger.error(error_msg)
        import traceback
        logger.error(traceback.format_exc())
        errors.append(error_msg)
    
    return restored_count, errors


async def close_database() -> bool:
    """Cleanup database resources on application shutdown.
    
    Performs the following cleanup steps:
    1. Releases all cached registry instances
    2. Closes database connections
    3. Logs cleanup status
    
    Returns:
        True if cleanup was successful, False otherwise
        
    Example:
        >>> success = await close_database()
        >>> if success:
        ...     print("Database connections closed successfully")
    """
    logger.info("Starting database cleanup...")
    
    try:
        # Step 1: Release all cached registry instances
        logger.info("Releasing registry instances...")
        factory = get_factory()
        cleared_count = await factory.clear_cache()
        logger.info(f"Released {cleared_count} registry instances from cache")
        
        # Step 2: Close database connections
        logger.info("Closing database connections...")
        await close_connection()
        logger.info("Database connections closed")
        
        logger.info("Database cleanup completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Database cleanup failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def get_database_status() -> dict:
    """Get current database status information.
    
    Returns:
        Dictionary containing database status information including:
        - healthy: Whether database connection is healthy
        - registry_count: Number of cached registry instances
        
    Example:
        >>> status = await get_database_status()
        >>> print(f"Database healthy: {status['healthy']}")
    """
    factory = get_factory()
    
    return {
        "healthy": await health_check(),
        "registry_count": factory.get_cached_count(),
    }
