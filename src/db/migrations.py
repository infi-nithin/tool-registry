import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from db.config import DatabaseConfig

logger = logging.getLogger(__name__)

# Path configuration
BASE_DIR = Path(__file__).parent.parent
ALEMBIC_INI_PATH = BASE_DIR / "alembic.ini"
MIGRATIONS_DIR = BASE_DIR / "migrations"


def _get_alembic_config() -> Config:
    """Create and configure Alembic config object.
    
    Returns:
        Configured Alembic Config instance
    """
    config = Config(str(ALEMBIC_INI_PATH))
    
    # Override with async database URL
    db_config = DatabaseConfig.from_url()
    config.set_main_option("sqlalchemy.url", db_config.sync_database_url)
    
    return config


async def run_migrations(target: str = "head") -> None:
    """Run pending database migrations.
    
    Applies all pending migrations up to the specified target revision.
    By default, upgrades to the latest version ("head").
    
    Args:
        target: Target revision to upgrade to. Defaults to "head" (latest).
                Can be a specific revision ID or relative notation (+1, -1).
    
    Raises:
        Exception: If migration fails
    
    Example:
        >>> await run_migrations()  # Upgrade to latest
        >>> await run_migrations("+1")  # Upgrade one version
        >>> await run_migrations("001")  # Upgrade to specific revision
    """
    logger.info(f"Running database migrations to target: {target}")
    
    def _run_upgrade():
        config = _get_alembic_config()
        command.upgrade(config, target)
    
    logger.info("Database migrations completed successfully")


async def create_migration(message: str, autogenerate: bool = True) -> Optional[str]:
    """Create a new migration script.
    
    Creates a new Alembic migration with the given message. If autogenerate
    is True (default), the migration will be generated based on model changes.
    
    Args:
        message: Description of the migration (used in filename and script)
        autogenerate: If True, auto-generate migration from model changes
    
    Returns:
        Path to the created migration file, or None if creation failed
    
    Raises:
        Exception: If migration creation fails
    
    Example:
        >>> # Auto-generate from model changes
        >>> path = await create_migration("Add user preferences table")
        >>> 
        >>> # Create empty migration
        >>> path = await create_migration("Data migration", autogenerate=False)
    """
    logger.info(f"Creating migration: {message} (autogenerate={autogenerate})")
    
    def _create():
        config = _get_alembic_config()
        
        # Create the revision
        command.revision(
            config,
            message=message,
            autogenerate=autogenerate,
        )
        
        # Get the newly created script
        script = ScriptDirectory.from_config(config)
        heads = list(script.get_heads())
        
        if heads:
            revision = script.get_revision(heads[0])
            return revision.path if revision else None
        return None
    
    loop = asyncio.get_event_loop()
    migration_path = await loop.run_in_executor(None, _create)
    
    if migration_path:
        logger.info(f"Migration created: {migration_path}")
    else:
        logger.warning("Migration created but could not determine path")
    
    return migration_path


async def get_current_revision() -> Optional[str]:
    """Get the current database migration revision.
    
    Returns the current revision hash from the database, or None if
    no migrations have been applied.
    
    Returns:
        Current revision hash, or None if database is not stamped
    
    Example:
        >>> current = await get_current_revision()
        >>> print(f"Database is at version: {current or 'none'}")
    """
    def _get_current():
        config = _get_alembic_config()
        
        # Get the script directory
        script = ScriptDirectory.from_config(config)
        
        # Get current revision from database
        from alembic.runtime import migration
        from sqlalchemy import create_engine
        
        db_config = DatabaseConfig.from_url()
        engine = create_engine(db_config.sync_database_url)
        
        with engine.connect() as connection:
            context = migration.MigrationContext.configure(connection)
            current_rev = context.get_current_revision()
        
        engine.dispose()
        return current_rev
    
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _get_current)
    except Exception as e:
        logger.warning(f"Could not get current revision: {e}")
        return None


async def stamp_database(revision: str = "head") -> None:
    """Stamp the database with a specific revision without running migrations.
    
    This is useful for initializing a database that already has the schema
    or for marking migrations as applied without actually running them.
    
    Args:
        revision: Revision to stamp. Defaults to "head" (latest).
                  Can be "base" to clear the stamp.
    
    Raises:
        Exception: If stamping fails
    
    Example:
        >>> # Mark database as having the latest schema
        >>> await stamp_database("head")
        >>> 
        >>> # Mark database as having a specific revision
        >>> await stamp_database("001")
    """
    logger.info(f"Stamping database with revision: {revision}")
    
    def _stamp():
        config = _get_alembic_config()
        command.stamp(config, revision)
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _stamp)
    
    logger.info("Database stamped successfully")


async def downgrade_migrations(target: str = "-1") -> None:
    """Downgrade database migrations.
    
    Reverts migrations down to the specified target. By default,
    downgrades one revision (-1).
    
    Args:
        target: Target revision to downgrade to. Defaults to "-1" (one back).
                Can be a specific revision ID or relative notation.
    
    Raises:
        Exception: If downgrade fails
    
    Example:
        >>> await downgrade_migrations()  # Downgrade one revision
        >>> await downgrade_migrations("-2")  # Downgrade two revisions
        >>> await downgrade_migrations("base")  # Downgrade to initial state
    """
    logger.info(f"Downgrading database migrations to target: {target}")
    
    def _run_downgrade():
        config = _get_alembic_config()
        command.downgrade(config, target)
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_downgrade)
    
    logger.info("Database downgrade completed successfully")


async def get_migration_history(verbose: bool = False) -> list[dict]:
    """Get the migration history.
    
    Returns a list of all migrations with their details.
    
    Args:
        verbose: If True, include additional details
    
    Returns:
        List of migration info dictionaries
    
    Example:
        >>> history = await get_migration_history()
        >>> for migration in history:
        ...     print(f"{migration['revision']}: {migration['description']}")
    """
    def _get_history():
        config = _get_alembic_config()
        script = ScriptDirectory.from_config(config)
        
        migrations = []
        for rev in script.walk_revisions():
            migration_info = {
                "revision": rev.revision,
                "down_revision": rev.down_revision,
                "description": rev.doc,
                "path": rev.path,
            }
            
            if verbose:
                migration_info.update({
                    "branch_labels": rev.branch_labels,
                    "dependencies": rev.dependencies,
                    "is_head": rev.is_head,
                    "is_base": rev.is_base,
                })
            
            migrations.append(migration_info)
        
        return migrations
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_history)


async def check_migrations_pending() -> bool:
    """Check if there are pending migrations to apply.
    
    Returns True if there are migrations that haven't been applied
    to the database yet.
    
    Returns:
        True if pending migrations exist, False otherwise
    
    Example:
        >>> if await check_migrations_pending():
        ...     print("Database needs migration!")
        ...     await run_migrations()
    """
    def _check():
        config = _get_alembic_config()
        script = ScriptDirectory.from_config(config)
        
        from alembic.runtime import migration
        from sqlalchemy import create_engine
        
        db_config = DatabaseConfig.from_url()
        engine = create_engine(db_config.sync_database_url)
        
        with engine.connect() as connection:
            context = migration.MigrationContext.configure(connection)
            current_rev = context.get_current_revision()
            
            # Get head revision
            head_revisions = list(script.get_heads())
            
            if not head_revisions:
                return False
            
            # Check if current is behind head
            if current_rev is None:
                return True
            
            # Get all revisions from current to head
            for head in head_revisions:
                if current_rev == head:
                    return False
                
                # Walk revisions to see if current is an ancestor
                for rev in script.walk_revisions(base=current_rev, head=head):
                    if rev.revision == head:
                        return True
            
            return False
    
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _check)
    except Exception as e:
        logger.error(f"Error checking migrations: {e}")
        return True  # Assume pending if we can't determine


def run_migrations_sync(target: str = "head") -> None:
    """Synchronous version of run_migrations.
    
    Useful for running migrations during application startup in
    synchronous contexts.
    
    Args:
        target: Target revision to upgrade to. Defaults to "head".
    
    Example:
        >>> # Run during application startup
        >>> run_migrations_sync()
    """
    logger.info(f"Running database migrations (sync) to target: {target}")
    
    config = _get_alembic_config()
    command.upgrade(config, target)
    
    logger.info("Database migrations completed successfully")


def create_migration_sync(message: str, autogenerate: bool = True) -> Optional[str]:
    """Synchronous version of create_migration.
    
    Args:
        message: Description of the migration
        autogenerate: Auto-generate from model changes
    
    Returns:
        Path to created migration file
    """
    logger.info(f"Creating migration (sync): {message}")
    
    config = _get_alembic_config()
    command.revision(config, message=message, autogenerate=autogenerate)
    
    script = ScriptDirectory.from_config(config)
    heads = list(script.get_heads())
    
    if heads:
        revision = script.get_revision(heads[0])
        path = revision.path if revision else None
        logger.info(f"Migration created: {path}")
        return path
    
    return None


async def init_alembic_if_needed() -> bool:
    """Initialize Alembic if it hasn't been initialized yet.
    
    Checks if alembic.ini exists and initializes if not.
    
    Returns:
        True if initialization was performed, False if already initialized
    
    Note:
        This function requires the alembic CLI to be available.
    """
    if ALEMBIC_INI_PATH.exists():
        return False
    
    logger.info("Initializing Alembic...")
    
    def _init():
        subprocess.run(
            ["alembic", "init", "migrations"],
            cwd=BASE_DIR,
            check=True,
        )
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _init)
    
    logger.info("Alembic initialized successfully")
    return True


# =============================================================================
# CLI Interface
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Database migration utilities"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Upgrade command
    upgrade_parser = subparsers.add_parser(
        "upgrade", help="Run database migrations"
    )
    upgrade_parser.add_argument(
        "--target", default="head",
        help="Target revision (default: head)"
    )
    
    # Downgrade command
    downgrade_parser = subparsers.add_parser(
        "downgrade", help="Revert database migrations"
    )
    downgrade_parser.add_argument(
        "--target", default="-1",
        help="Target revision (default: -1)"
    )
    
    # Create command
    create_parser = subparsers.add_parser(
        "create", help="Create a new migration"
    )
    create_parser.add_argument(
        "message", help="Migration description"
    )
    create_parser.add_argument(
        "--no-autogenerate", action="store_true",
        help="Don't auto-generate from models"
    )
    
    # Current command
    subparsers.add_parser("current", help="Show current revision")
    
    # History command
    history_parser = subparsers.add_parser(
        "history", help="Show migration history"
    )
    history_parser.add_argument(
        "--verbose", action="store_true",
        help="Show verbose output"
    )
    
    # Pending command
    subparsers.add_parser(
        "pending", help="Check for pending migrations"
    )
    
    # Stamp command
    stamp_parser = subparsers.add_parser(
        "stamp", help="Stamp database with revision"
    )
    stamp_parser.add_argument(
        "revision", default="head", nargs="?",
        help="Revision to stamp (default: head)"
    )
    
    args = parser.parse_args()
    
    async def _main():
        if args.command == "upgrade":
            await run_migrations(args.target)
        elif args.command == "downgrade":
            await downgrade_migrations(args.target)
        elif args.command == "create":
            path = await create_migration(
                args.message,
                autogenerate=not args.no_autogenerate
            )
            if path:
                print(f"Created: {path}")
        elif args.command == "current":
            current = await get_current_revision()
            print(f"Current revision: {current or 'none'}")
        elif args.command == "history":
            history = await get_migration_history(verbose=args.verbose)
            for migration in history:
                print(f"{migration['revision']}: {migration['description']}")
        elif args.command == "pending":
            pending = await check_migrations_pending()
            print(f"Pending migrations: {'yes' if pending else 'no'}")
        elif args.command == "stamp":
            await stamp_database(args.revision)
        else:
            parser.print_help()
    
    asyncio.run(_main())
