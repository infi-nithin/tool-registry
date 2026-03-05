"""MCP Server registry models.

Provides SQLAlchemy models for the MCP server registry, replacing
in-memory storage with PostgreSQL persistence.
"""

import enum
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import BaseModel

if TYPE_CHECKING:
    from db.models.audit import AuditServerOperationDB, AuditToolInvocationDB


class MCPServerStatus(str, enum.Enum):
    """Operational status of MCP servers and their tags.

    Matches the mcp_server_status enum in the database schema.
    """

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class MCPServerDB(BaseModel):
    """MCP Server registry model.

    Stores the main MCP server registry information, replacing
    `_mounted_servers`, `_server_configs`, and `_server_status` in-memory storage.

    Attributes:
        server_name: Unique server identifier (e.g., "weather-api")
        spec_link: URL to OpenAPI specification
        base_url: Base URL for API calls
        description: Human-readable description
        headers: Default headers for API calls (stored as JSONB)
        status: Overall server status
        tool_count: Cached count of available tools
        tags: Related tag records
        tool_invocations: Related audit trail for tool invocations
        operations: Related audit trail for server operations

    Example:
        >>> server = MCPServerDB(
        ...     server_name="weather-api",
        ...     spec_link="https://example.com/openapi.json",
        ...     base_url="https://api.example.com",
        ...     status=MCPServerStatus.ACTIVE,
        ... )
    """

    __tablename__ = "mcp_servers"

    # Core server information
    server_name: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="Unique server identifier (e.g., 'weather-api')",
    )
    spec_link: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="URL to OpenAPI specification",
    )
    base_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        comment="Base URL for API calls",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable description",
    )

    # Configuration storage
    headers: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default="{}",
        nullable=True,
        comment="Default headers for API calls",
    )

    # Status tracking
    status: Mapped[MCPServerStatus] = mapped_column(
        String(20),
        default=MCPServerStatus.ACTIVE,
        nullable=False,
        index=True,
        comment="Overall server status",
    )
    tool_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Cached count of available tools",
    )

    # Relationships
    tags: Mapped[List["MCPServerTagDB"]] = relationship(
        "MCPServerTagDB",
        back_populates="server",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    tool_invocations: Mapped[List["AuditToolInvocationDB"]] = relationship(
        "AuditToolInvocationDB",
        back_populates="server",
        lazy="dynamic",
    )
    operations: Mapped[List["AuditServerOperationDB"]] = relationship(
        "AuditServerOperationDB",
        back_populates="server",
        lazy="dynamic",
    )

    # Table constraints and indexes
    __table_args__ = (
        # Ensure valid base_url format (must start with http:// or https://)
        # Note: Full validation should be done at application level
        Index(
            "idx_mcp_servers_name",
            "server_name",
            postgresql_where="is_deleted = false",
        ),
        Index(
            "idx_mcp_servers_status",
            "status",
            postgresql_where="is_deleted = false",
        ),
        Index(
            "idx_mcp_servers_active",
            "status",
            "created_at",
            postgresql_where="is_deleted = false",
        ),
        Index(
            "idx_mcp_servers_deleted",
            "is_deleted",
            "deleted_at",
            postgresql_where="is_deleted = true",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MCPServerDB("
            f"id={self.id}, "
            f"server_name={self.server_name}, "
            f"status={self.status.value}"
            f")>"
        )


class MCPServerTagDB(BaseModel):
    """MCP Server tag status model.

    Stores tag-level status for each server, replacing the `_server_tags`
    dictionary from in-memory storage.

    Attributes:
        server_id: Foreign key to the parent server
        tag_name: Tag name from OpenAPI spec
        status: Tag-specific operational status
        server: Parent server relationship

    Example:
        >>> tag = MCPServerTagDB(
        ...     server_id=server.id,
        ...     tag_name="weather",
        ...     status=MCPServerStatus.ACTIVE,
        ... )
    """

    __tablename__ = "mcp_server_tags"

    # Foreign key to parent server
    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to server",
    )

    # Tag information
    tag_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Tag name from OpenAPI spec",
    )
    status: Mapped[MCPServerStatus] = mapped_column(
        String(20),
        default=MCPServerStatus.ACTIVE,
        nullable=False,
        comment="Tag-specific status",
    )

    # Relationships
    server: Mapped["MCPServerDB"] = relationship(
        "MCPServerDB",
        back_populates="tags",
    )

    # Table constraints and indexes
    __table_args__ = (
        # Unique constraint on server_id + tag_name combination
        UniqueConstraint(
            "server_id",
            "tag_name",
            name="uq_server_tag",
        ),
        # Index for server lookups
        Index(
            "idx_mcp_server_tags_server",
            "server_id",
        ),
        # Index for tag name lookups
        Index(
            "idx_mcp_server_tags_name",
            "tag_name",
        ),
        # Index for active tags by server
        Index(
            "idx_mcp_server_tags_active",
            "server_id",
            "status",
            postgresql_where="is_deleted = false",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MCPServerTagDB("
            f"id={self.id}, "
            f"server_id={self.server_id}, "
            f"tag_name={self.tag_name}, "
            f"status={self.status.value}"
            f")>"
        )