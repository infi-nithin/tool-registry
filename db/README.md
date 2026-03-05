# Database Module

This module provides PostgreSQL database integration for the Tool Registry application, including SQLAlchemy models, connection management, migrations, and startup initialization.

## Overview

The database module consists of the following components:

- **Models** (`models/`): SQLAlchemy ORM models for MCP servers and audit logs
- **Configuration** (`config.py`): Database configuration management
- **Connection** (`connection.py`): Async database engine and session management
- **Migrations** (`migrations.py`): Alembic migration utilities
- **Startup** (`startup.py`): Application startup initialization

## Setup

### 1. Install Dependencies

Database dependencies are included in `pyproject.toml`:

```bash
uv pip install -e "."
```

Key dependencies:
- `sqlalchemy[asyncio]` - SQLAlchemy with async support
- `asyncpg` - PostgreSQL async driver
- `alembic` - Database migrations
- `psycopg2-binary` - Sync PostgreSQL driver (for alembic)

### 2. Configure Environment Variables

Copy the example environment file and update it with your database credentials:

```bash
cp .env.example .env
```

Required database environment variables:

```bash
# Option 1: Full database URL
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/tool_registry

# Option 2: Individual components (used if DATABASE_URL not set)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=tool_registry
DB_USER=tool_registry_user
DB_PASSWORD=your_secure_password
```

### 3. Create Database

Create the PostgreSQL database and user:

```bash
# Connect to PostgreSQL
psql -U postgres

# Create database
CREATE DATABASE tool_registry;

# Create user (optional, for dedicated application user)
CREATE USER tool_registry_user WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE tool_registry TO tool_registry_user;
```

## Running Migrations

### From Command Line

Using the `db/migrations.py` module directly:

```bash
# Run all pending migrations
python -c "import asyncio; from db.migrations import run_migrations; asyncio.run(run_migrations())"

# Check current revision
python -c "import asyncio; from db.migrations import get_current_revision; print(asyncio.run(get_current_revision()))"

# Create new migration
python -c "import asyncio; from db.migrations import create_migration; asyncio.run(create_migration('Add new feature'))"
```

### Using Alembic CLI

```bash
# Run migrations
alembic upgrade head

# Downgrade one revision
alembic downgrade -1

# Create new migration
alembic revision --autogenerate -m "Add new feature"

# View current revision
alembic current

# View history
alembic history
```

### Programmatically

Migrations are automatically run during application startup via `db/startup.py`:

```python
from db.startup import initialize_database

# This runs migrations, initializes engine, and verifies connection
success = await initialize_database()
```

## Connection Pooling

Connection pooling settings can be configured via environment variables:

```bash
DB_POOL_SIZE=10        # Number of connections to keep in pool
DB_MAX_OVERFLOW=20     # Maximum overflow connections
```

Default values:
- Pool size: 10 connections
- Max overflow: 20 connections
- Pool timeout: 30 seconds
- Pool pre-ping: Enabled (verifies connections before use)

## Usage Examples

### Getting a Database Session

```python
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from db.connection import get_db_session

@app.get("/items")
async def get_items(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Item))
    return result.scalars().all()
```

### Using the MCP Registry

```python
from service.mcp_registry_factory import get_registry
from db.connection import get_db_session

@app.post("/servers")
async def create_server(
    config: MCPServerConfig,
    db: AsyncSession = Depends(get_db_session),
):
    registry = await get_registry(db)
    success, tool_count, message = await registry.mount_server(config)
    return {"success": success, "tools": tool_count}
```

### Manual Session Management

```python
from db.connection import async_session_factory

async def manual_operation():
    async with async_session_factory() as session:
        # Operations are automatically committed on success
        result = await session.execute(select(Server))
        servers = result.scalars().all()
        return servers
        # Session is automatically closed
```

## Database Schema

The database schema includes the following main tables:

### MCP Servers

- `mcp_servers`: Main server configurations
- `mcp_server_tags`: Tag associations for servers

### Audit Logging

- `audit_log_entries`: Operation audit trail
- `audit_tool_executions`: Tool execution tracking
- `data_changelog`: Data change history

See `docs/database_schema.md` for detailed schema documentation.

## Troubleshooting

### Connection Issues

1. Verify PostgreSQL is running:
   ```bash
   pg_isready -h localhost -p 5432
   ```

2. Check connection string format:
   - Async URL: `postgresql+asyncpg://...`
   - Sync URL: `postgresql://...` or `postgresql+psycopg2://...`

3. Verify user permissions:
   ```sql
   \c tool_registry
   \dp
   ```

### Migration Issues

1. Check current revision:
   ```bash
   alembic current
   ```

2. View migration history:
   ```bash
   alembic history --verbose
   ```

3. Stamp database (if already has schema):
   ```bash
   alembic stamp head
   ```

### Performance Tuning

1. Adjust pool size based on load:
   ```bash
   DB_POOL_SIZE=20
   DB_MAX_OVERFLOW=30
   ```

2. Enable query logging (development only):
   ```python
   # In db/connection.py, set echo=True
   engine = create_async_engine(..., echo=True)
   ```

## Testing

Run database tests with:

```bash
# Run migrations in test environment
DATABASE_URL=postgresql+asyncpg://test_user:test_pass@localhost/test_db python -m pytest

# Or use test fixtures that handle database setup
pytest tests/ -v --db-url=postgresql+asyncpg://localhost/test_db
```

## Architecture Notes

- All database operations are async using SQLAlchemy's async support
- Sessions are managed via FastAPI's dependency injection system
- Connection pooling is configured for production workloads
- Migrations use Alembic with both sync and async support
- Audit logging is integrated at the service layer
