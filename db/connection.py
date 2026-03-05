"""Database connection management for MCP Server Registry.

This module provides SQLAlchemy engine configuration, async session management,
and FastAPI dependency injection for database sessions.
"""

from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import text

from db.config import DatabaseConfig

# Global engine and session factory instances
engine: Optional[AsyncEngine] = None
async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


async def init_engine(config: Optional[DatabaseConfig] = None) -> AsyncEngine:
    """Initialize the async SQLAlchemy engine.

    Args:
        config: Database configuration. If None, uses environment variables.

    Returns:
        Configured async engine instance

    Example:
        >>> engine = await init_engine()
    """
    global engine

    if engine is not None:
        return engine

    cfg = config or DatabaseConfig.from_url()

    engine = create_async_engine(
        cfg.async_database_url,
        pool_size=cfg.pool_size,
        max_overflow=cfg.max_overflow,
        pool_timeout=cfg.pool_timeout,
        pool_pre_ping=True,  # Verify connections before using from pool
        echo=False,  # Set to True for SQL query logging
        future=True,
    )

    return engine


async def init_session_factory(
    engine_instance: Optional[AsyncEngine] = None,
) -> async_sessionmaker[AsyncSession]:
    """Initialize the async session factory.

    Args:
        engine_instance: SQLAlchemy engine. If None, initializes a new one.

    Returns:
        Configured async session factory

    Example:
        >>> session_factory = await init_session_factory()
    """
    global async_session_factory

    if async_session_factory is not None:
        return async_session_factory

    eng = engine_instance or await init_engine()

    async_session_factory = async_sessionmaker(
        bind=eng,
        class_=AsyncSession,
        expire_on_commit=False,  # Don't expire objects after commit
        autocommit=False,
        autoflush=False,
    )

    return async_session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database session injection.

    Provides an async database session that is automatically committed
    on successful request completion or rolled back on exception.

    Yields:
        AsyncSession: Database session for request handling

    Example:
        >>> @app.get("/servers")
        ... async def list_servers(db: AsyncSession = Depends(get_db_session)):
        ...     result = await db.execute(select(MCPServerDB))
        ...     return result.scalars().all()
    """
    global async_session_factory

    if async_session_factory is None:
        await init_session_factory()

    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def health_check() -> bool:
    """Check database connectivity and health.

    Returns:
        True if database is accessible, False otherwise

    Example:
        >>> is_healthy = await health_check()
        >>> if not is_healthy:
        ...     logger.error("Database connection failed")
    """
    global engine

    if engine is None:
        return False

    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            return result.scalar() == 1
    except Exception:
        return False


async def close_connection() -> None:
    """Close database connection pool and cleanup resources.

    Should be called during application shutdown to properly release
    database connections.

    Example:
        >>> @app.on_event("shutdown")
        ... async def shutdown():
        ...     await close_connection()
    """
    global engine, async_session_factory

    if engine is not None:
        await engine.dispose()
        engine = None

    async_session_factory = None


async def init_database(
    config: Optional[DatabaseConfig] = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Initialize complete database infrastructure.

    Convenience function to initialize both engine and session factory.

    Args:
        config: Database configuration. If None, uses environment variables.

    Returns:
        Tuple of (engine, session_factory)

    Example:
        >>> engine, session_factory = await init_database()
    """
    eng = await init_engine(config)
    factory = await init_session_factory(eng)
    return eng, factory