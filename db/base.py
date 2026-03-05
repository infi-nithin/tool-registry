"""Base model with maintenance columns for MCP Server Registry.

Provides SQLAlchemy declarative base and base model class with common
maintenance columns for audit trails, soft deletes, and optimistic locking.
"""

import uuid
from datetime import datetime
from typing import Any, ClassVar, Dict, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    declared_attr,
)


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy models.

    Provides the foundation for type annotation-based mapped columns.
    """

    pass


class BaseModel(Base):
    """Abstract base model with maintenance columns.

    Provides common columns for:
    - Primary key (UUID)
    - Timestamps (created_at, updated_at)
    - Audit trail (created_by, updated_by)
    - Soft delete (is_deleted, deleted_at)
    - Optimistic locking (version)

    Attributes:
        id: UUID primary key
        created_at: Timestamp when record was created
        updated_at: Timestamp of last update
        created_by: User/system that created the record
        updated_by: User/system that last updated the record
        is_deleted: Soft delete flag
        deleted_at: Timestamp of soft deletion
        version: Optimistic locking version number

    Example:
        >>> class MyModel(BaseModel):
        ...     name: Mapped[str] = mapped_column(String(255))
    """

    __abstract__ = True

    # Primary key using UUID
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
        comment="Unique identifier",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Creation timestamp",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="Last update timestamp",
    )

    # Audit trail - WHO
    created_by: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="User/system that created the record",
    )
    updated_by: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="User/system that last updated the record",
    )

    # Soft delete support
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
        comment="Soft delete flag",
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Soft delete timestamp",
    )

    # Optimistic locking
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="Optimistic locking version",
    )

    @declared_attr.directive
    @classmethod
    def __tablename__(cls) -> str:
        """Generate table name automatically from class name.

        Converts CamelCase to snake_case and removes DB suffix.

        Examples:
            MCPServerDB -> mcp_servers
            AuditSessionDB -> audit_sessions
            MyModel -> my_models
        """
        name = cls.__name__
        # Remove DB suffix if present
        if name.endswith("DB"):
            name = name[:-2]

        # Convert CamelCase to snake_case
        result = []
        for i, char in enumerate(name):
            if char.isupper() and i > 0:
                if name[i - 1].islower() or (
                    i < len(name) - 1 and name[i + 1].islower()
                ):
                    result.append("_")
            result.append(char.lower())

        # Pluralize common words
        table_name = "".join(result)
        if not table_name.endswith("s"):
            table_name += "s"

        return table_name

    def to_dict(self, exclude: Optional[set[str]] = None) -> Dict[str, Any]:
        """Convert model instance to dictionary.

        Args:
            exclude: Set of column names to exclude from output

        Returns:
            Dictionary representation of the model

        Example:
            >>> server = MCPServerDB(server_name="test")
            >>> server.to_dict()
            {'id': '...', 'server_name': 'test', ...}
            >>> server.to_dict(exclude={'created_by', 'updated_by'})
            {'id': '...', 'server_name': 'test', ...}  # without audit fields
        """
        exclude = exclude or set()
        result: Dict[str, Any] = {}

        for column in self.__table__.columns:
            if column.name in exclude:
                continue

            value = getattr(self, column.name)

            # Convert UUID to string
            if isinstance(value, uuid.UUID):
                value = str(value)
            # Convert datetime to ISO format string
            elif isinstance(value, datetime):
                value = value.isoformat()

            result[column.name] = value

        return result

    def soft_delete(self, deleted_by: Optional[str] = None) -> None:
        """Soft delete the record.

        Sets is_deleted to True and records deletion timestamp.
        Does not actually remove the record from the database.

        Args:
            deleted_by: User/system performing the deletion

        Example:
            >>> server.soft_delete(deleted_by="admin")
            >>> session.commit()
        """
        self.is_deleted = True
        self.deleted_at = datetime.now()
        self.updated_by = deleted_by
        self.version = (self.version or 0) + 1

    def restore(self, restored_by: Optional[str] = None) -> None:
        """Restore a soft-deleted record.

        Args:
            restored_by: User/system performing the restore

        Example:
            >>> server.restore(restored_by="admin")
            >>> session.commit()
        """
        self.is_deleted = False
        self.deleted_at = None
        self.updated_by = restored_by
        self.version = (self.version or 0) + 1

    def __repr__(self) -> str:
        """String representation of the model instance."""
        return f"<{self.__class__.__name__}(id={self.id})>"