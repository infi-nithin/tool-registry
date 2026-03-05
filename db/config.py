"""Database configuration module for MCP Server Registry.

This module provides configuration management for PostgreSQL connections
with support for environment variables and connection pooling.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DatabaseConfig:
    """PostgreSQL database configuration.

    Supports environment variable configuration with sensible defaults.
    Can be initialized from DATABASE_URL or individual connection parameters.

    Attributes:
        host: Database host address
        port: Database port number
        database: Database name
        user: Database username
        password: Database password
        pool_size: Connection pool size
        max_overflow: Maximum number of connections to overflow
        pool_timeout: Timeout for getting connection from pool (seconds)
        ssl_mode: SSL mode for connection (disable, allow, prefer, require, verify-ca, verify-full)
    """

    host: str = field(default_factory=lambda: os.getenv("DB_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("DB_PORT", "5432")))
    database: str = field(default_factory=lambda: os.getenv("DB_NAME", "mcp_registry"))
    user: str = field(default_factory=lambda: os.getenv("DB_USER", "postgres"))
    password: str = field(default_factory=lambda: os.getenv("DB_PASSWORD", ""))
    pool_size: int = field(default_factory=lambda: int(os.getenv("DB_POOL_SIZE", "5")))
    max_overflow: int = field(default_factory=lambda: int(os.getenv("DB_MAX_OVERFLOW", "10")))
    pool_timeout: int = field(default_factory=lambda: int(os.getenv("DB_POOL_TIMEOUT", "30")))
    ssl_mode: Optional[str] = field(default_factory=lambda: os.getenv("DB_SSL_MODE", "prefer"))

    @classmethod
    def from_url(cls, url: Optional[str] = None) -> "DatabaseConfig":
        """Create configuration from DATABASE_URL environment variable or provided URL.

        Args:
            url: PostgreSQL connection URL. If None, reads from DATABASE_URL env var.

        Returns:
            DatabaseConfig instance parsed from URL

        Example:
            >>> config = DatabaseConfig.from_url("postgresql://user:pass@localhost:5432/dbname")
        """
        database_url = url or os.getenv("DATABASE_URL")

        if not database_url:
            return cls()

        # Parse postgresql://user:password@host:port/database format
        # Handle postgresql+asyncpg:// prefix
        clean_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        clean_url = clean_url.replace("postgresql://", "")

        # Extract credentials and host info
        if "@" in clean_url:
            credentials, host_info = clean_url.split("@", 1)
            if ":" in credentials:
                user, password = credentials.split(":", 1)
            else:
                user = credentials
                password = ""
        else:
            user = "postgres"
            password = ""
            host_info = clean_url

        # Extract host, port, and database
        if "/" in host_info:
            host_port, database = host_info.split("/", 1)
        else:
            host_port = host_info
            database = "mcp_registry"

        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 5432

        return cls(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )

    @property
    def async_database_url(self) -> str:
        """Build asyncpg connection URL.

        Returns:
            Connection string for asyncpg driver
        """
        ssl_param = f"?ssl={self.ssl_mode}" if self.ssl_mode else ""
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}{ssl_param}"
        )

    @property
    def sync_database_url(self) -> str:
        """Build synchronous connection URL.

        Returns:
            Connection string for psycopg2 driver
        """
        ssl_param = f"?sslmode={self.ssl_mode}" if self.ssl_mode else ""
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}{ssl_param}"
        )