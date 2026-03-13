import os
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
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    # Construct from components
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    name = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")

    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


async def run_alembic_migrations() -> None:
    from alembic.config import Config
    from alembic import command

    # Get the path to alembic.ini - look in multiple locations
    # Starting from the project root (parent of src)
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    possible_paths = [
        os.path.join(project_root, "alembic.ini"),
        os.path.join(os.getcwd(), "alembic.ini"),
    ]

    alembic_ini_path = None
    for path in possible_paths:
        normalized = os.path.normpath(path)
        if os.path.exists(normalized):
            alembic_ini_path = normalized
            break

    if alembic_ini_path:
        alembic_cfg = Config(alembic_ini_path)
        # Ensure the script location is absolute
        alembic_cfg.set_main_option(
            "script_location", os.path.join(project_root, "alembic")
        )

        # Run Alembic upgrade synchronously
        command.upgrade(alembic_cfg, "head")
    else:
        raise RuntimeError(f"Alembic config not found in any of: {possible_paths}")


async def init_db(
    pool_size: int = 10,
    max_overflow: int = 20,
    run_migrations: bool = True,
) -> AsyncEngine:
    global engine, async_session_factory

    db_url = get_database_url()

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
        except Exception:
            # Don't fail - tables might already exist
            pass

    return engine


async def get_engine() -> Optional[AsyncEngine]:
    return engine


async def get_session() -> AsyncGenerator[AsyncSession, None]:
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
    if async_session_factory is None:
        await init_db()

    return async_session_factory()


async def close_db() -> None:
    global engine, async_session_factory

    if engine:
        await engine.dispose()
        engine = None
        async_session_factory = None
