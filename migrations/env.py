"""Alembic migration environment with async PostgreSQL support.

This module configures Alembic for use with async SQLAlchemy and PostgreSQL.
It provides both synchronous and asynchronous migration capabilities.
"""

import asyncio
import logging
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context

# Add the project root to the path
sys.path.insert(0, ".")

from db.base import Base
from db.config import DatabaseConfig
from db.models import (
    MCPServerDB,
    MCPServerTagDB,
    AuditCorrelation,
    AuditSessionDB,
    AuditToolInvocationDB,
    AuditServerOperationDB,
)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# Get database URL from DatabaseConfig
db_config = DatabaseConfig.from_url()
DATABASE_URL = db_config.async_database_url
SYNC_DATABASE_URL = db_config.sync_database_url


def get_database_url():
    """Get the async database URL from configuration."""
    return DATABASE_URL


def get_sync_database_url():
    """Get the synchronous database URL for Alembic operations."""
    return SYNC_DATABASE_URL


# Set the database URL in the alembic config
config.set_main_option("sqlalchemy.url", SYNC_DATABASE_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_sync_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version",
        include_schemas=True,
        # Compare type to detect column type changes
        compare_type=True,
        # Compare server default to detect default value changes
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Execute migrations with the given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="alembic_version",
        include_schemas=True,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online_async() -> None:
    """Run migrations in 'online' mode using async engine."""
    # For async operations, we need to use sync engine for Alembic
    # Alembic doesn't fully support async operations natively
    connectable = create_async_engine(
        DATABASE_URL,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online_sync() -> None:
    """Run migrations in 'online' mode using sync engine.

    This is the preferred method for Alembic as it provides
    full transaction support.
    """
    # Use sync engine for Alembic operations
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_sync_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    # Use sync engine by default for better compatibility
    run_migrations_online_sync()


if context.is_offline_mode():
    logger.info("Running migrations in offline mode")
    run_migrations_offline()
else:
    logger.info("Running migrations in online mode")
    run_migrations_online()
