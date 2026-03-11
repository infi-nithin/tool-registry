"""Database connection and session management.

This module provides async SQLAlchemy engine and session factory
for database operations using Alembic for migrations.
"""

import os
import logging
from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool
from dotenv import load_dotenv

load_dotenv()

# Create async engine
engine: Optional[AsyncEngine] = None

# Session factory
async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_database_url() -> str:
    """Get the database URL from environment or construct from components.

    Returns:
        str: The async database URL for SQLAlchemy
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    # Construct from components
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "tool_registry")
    user = os.getenv("DB_USER", "tool_registry_user")
    password = os.getenv("DB_PASSWORD", "your_secure_password")

    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


async def run_alembic_migrations() -> None:
    """Run Alembic migrations.

    Uses Alembic to upgrade the database schema.
    This creates all tables defined in the models.
    """
    from alembic.config import Config
    from alembic import command

    # Get the path to alembic.ini - look in multiple locations
    # Starting from the project root (parent of src)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    possible_paths = [
        os.path.join(project_root, 'alembic.ini'),
        os.path.join(os.getcwd(), 'alembic.ini'),
    ]
    
    alembic_ini_path = None
    for path in possible_paths:
        normalized = os.path.normpath(path)
        if os.path.exists(normalized):
            alembic_ini_path = normalized
            break
    
    if alembic_ini_path:
        logging.info(f"Running Alembic migrations from: {alembic_ini_path}")
        alembic_cfg = Config(alembic_ini_path)
        # Ensure the script location is absolute
        alembic_cfg.set_main_option('script_location', os.path.join(project_root, 'alembic'))
        
        # Run Alembic upgrade synchronously
        command.upgrade(alembic_cfg, "head")
    else:
        raise RuntimeError(f"Alembic config not found in any of: {possible_paths}")


async def init_db(
    pool_size: int = 10,
    max_overflow: int = 20,
    run_migrations: bool = True,
) -> AsyncEngine:
    """Initialize the database engine and session factory.

    Note: The database must exist before calling this function.
    Use Alembic to create tables (they will be created automatically).

    Args:
        pool_size: Connection pool size
        max_overflow: Max overflow connections
        run_migrations: If True, runs Alembic migrations to create tables

    Returns:
        The configured async engine
    
    Raises:
        RuntimeError: If Alembic config is not found
    """
    global engine, async_session_factory

    db_url = get_database_url()
    logging.info(f"Connecting to database: {db_url}")
    
    engine = create_async_engine(
        db_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        poolclass=AsyncAdaptedQueuePool,
    )

    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Run Alembic migrations to create tables
    if run_migrations:
        try:
            await run_alembic_migrations()
        except Exception as e:
            logging.warning(f"Alembic migration failed: {e}")
            # Don't fail - tables might already exist
            pass

    return engine


async def get_engine() -> Optional[AsyncEngine]:
    """Get the current database engine.

    Returns:
        The async engine or None if not initialized
    """
    return engine


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session.

    Yields:
        An AsyncSession for database operations

    Example:
        async for session in get_session():
            result = await session.execute(...)
    """
    if async_session_factory is None:
        await init_db()

    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_session_context() -> AsyncSession:
    """Get an async session context manager.

    Returns:
        An AsyncSession context manager

    Example:
        async with get_session_context() as session:
            result = await session.execute(...)
    """
    if async_session_factory is None:
        await init_db()

    return async_session_factory()


async def close_db() -> None:
    """Close the database engine and cleanup connections."""
    global engine, async_session_factory

    if engine:
        await engine.dispose()
        engine = None
        async_session_factory = None
