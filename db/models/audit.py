"""Audit trail models for MCP Server Registry.

Provides SQLAlchemy models for comprehensive audit logging including
session tracking, tool invocations, and server lifecycle operations.
"""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, BaseModel

if TYPE_CHECKING:
    from db.models.mcp_server import MCPServerDB


class AuditOperationStatus(str, enum.Enum):
    """Status of audited operations.

    Matches the audit_operation_status enum in the database schema.
    """

    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"


class ServerOperationType(str, enum.Enum):
    """Types of operations performed on MCP servers.

    Matches the server_operation_type enum in the database schema.
    """

    MOUNT = "MOUNT"
    UNMOUNT = "UNMOUNT"
    ENABLE = "ENABLE"
    DISABLE = "DISABLE"
    REMOVE = "REMOVE"
    UPDATE_CONFIG = "UPDATE_CONFIG"


class AuditSessionDB(Base):
    """Session tracking model for audit trail correlation.

    Tracks user sessions to correlate audit events across a single session.
    This is not a BaseModel subclass because it doesn't need soft delete
    or optimistic locking - audit records are immutable.

    Attributes:
        session_id: External session identifier
        user_id: Authenticated user identifier
        api_key_id: API key used for authentication
        client_ip: Client IP address
        user_agent: Client user agent string
        started_at: Session start timestamp
        ended_at: Session end timestamp
        session_metadata: Additional session context (JSONB)

    Example:
        >>> session = AuditSessionDB(
        ...     session_id="sess-abc-123",
        ...     user_id="user@example.com",
        ...     client_ip="192.168.1.1",
        ... )
    """

    __tablename__ = "audit_sessions"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique identifier",
    )

    # Session identifiers
    session_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="External session identifier",
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="Authenticated user identifier",
    )
    api_key_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="API key used for authentication",
    )

    # Client information (WHO)
    client_ip: Mapped[Optional[str]] = mapped_column(
        INET,
        nullable=True,
        comment="Client IP address",
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Client user agent string",
    )

    # Timestamps (WHEN)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="Session start timestamp",
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Session end timestamp",
    )

    # Additional context
    session_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        server_default="{}",
        nullable=True,
        comment="Additional session context",
    )

    # Table indexes
    __table_args__ = (
        Index(
            "idx_audit_sessions_started",
            "started_at",
        ),
    )

    def __repr__(self) -> str:
        return f"<AuditSessionDB(session_id={self.session_id}, user_id={self.user_id})>"


class AuditToolInvocationDB(Base):
    """MCP tool invocation audit log model.

    Comprehensive audit log for MCP tool invocations (high-write volume).
    This table is partitioned by partition_date for performance.

    Attributes:
        correlation_id: Correlation ID for request tracing
        session_id: Link to session
        server_id: Source MCP server
        tool_name: Name of invoked tool
        arguments: Tool arguments (sanitized, JSONB)
        result_status: Operation result status
        result_summary: Summary of result
        duration_ms: Execution duration in milliseconds
        error_type: Type of error if failed
        error_message: Error message if failed
        stack_trace: Stack trace if failed
        tags: Associated tags (JSONB)
        invoked_at: Invocation timestamp
        partition_date: Partition key for table partitioning
        server: Relationship to MCP server

    Example:
        >>> invocation = AuditToolInvocationDB(
        ...     correlation_id="corr-xyz-789",
        ...     session_id="sess-abc-123",
        ...     tool_name="get_weather",
        ...     result_status=AuditOperationStatus.SUCCESS,
        ...     duration_ms=150.5,
        ... )
    """

    __tablename__ = "audit_tool_invocations"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique identifier",
    )

    # Correlation and session (WHO/WHAT context)
    correlation_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("audit_correlations.correlation_id"),
        nullable=True,
        index=True,
        comment="Correlation ID for request tracing",
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("audit_sessions.session_id"),
        nullable=True,
        index=True,
        comment="Link to session",
    )

    # Server reference
    server_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mcp_servers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Source MCP server",
    )

    # Tool information (WHAT)
    tool_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        index=True,
        comment="Name of invoked tool",
    )
    arguments: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Tool arguments (sanitized)",
    )

    # Result information
    result_status: Mapped[AuditOperationStatus] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="Operation result status",
    )
    result_summary: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Summary of result",
    )
    duration_ms: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Execution duration in milliseconds",
    )

    # Error information
    error_type: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="Type of error if failed",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if failed",
    )
    stack_trace: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Stack trace if failed",
    )

    # Additional metadata
    tags: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Associated tags",
    )

    # Timestamps (WHEN)
    invoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="Invocation timestamp",
    )

    # Partitioning key
    partition_date: Mapped[date] = mapped_column(
        Date,
        server_default=func.current_date(),
        nullable=False,
        comment="Partition key for table partitioning",
    )

    # Relationships
    server: Mapped[Optional["MCPServerDB"]] = relationship(
        "MCPServerDB",
        back_populates="tool_invocations",
    )

    # Table indexes
    __table_args__ = (
        # Tool performance analysis indexes
        Index(
            "idx_audit_tool_invocations_tool",
            "tool_name",
            "invoked_at",
        ),
        Index(
            "idx_audit_tool_invocations_duration",
            "duration_ms",
            postgresql_where="duration_ms > 1000",
        ),
        # Error analysis
        Index(
            "idx_audit_tool_invocations_error",
            "result_status",
            "error_type",
            postgresql_where="result_status = 'FAILURE'",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditToolInvocationDB("
            f"tool_name={self.tool_name}, "
            f"status={self.result_status.value}, "
            f"duration_ms={self.duration_ms:.2f}"
            f")>"
        )


class AuditServerOperationDB(Base):
    """MCP server lifecycle operation audit log model.

    Audit log for MCP server lifecycle operations like mount, unmount,
    enable, disable, remove, and config updates.

    Attributes:
        correlation_id: Correlation ID for request tracing
        session_id: Link to session
        server_id: Target MCP server
        operation_type: Type of operation performed
        operation_details: Operation-specific details (JSONB)
        result_status: Operation result status
        error_message: Error message if failed
        duration_ms: Operation duration in milliseconds
        executed_at: Operation timestamp
        partition_date: Partition key
        server: Relationship to MCP server

    Example:
        >>> operation = AuditServerOperationDB(
        ...     correlation_id="corr-xyz-789",
        ...     session_id="sess-abc-123",
        ...     operation_type=ServerOperationType.MOUNT,
        ...     result_status=AuditOperationStatus.SUCCESS,
        ...     duration_ms=250.0,
        ... )
    """

    __tablename__ = "audit_server_operations"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique identifier",
    )

    # Correlation and session
    correlation_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("audit_correlations.correlation_id"),
        nullable=True,
        index=True,
        comment="Correlation ID for request tracing",
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        ForeignKey("audit_sessions.session_id"),
        nullable=True,
        index=True,
        comment="Link to session",
    )

    # Server reference
    server_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mcp_servers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Target MCP server",
    )

    # Operation details (WHAT)
    operation_type: Mapped[ServerOperationType] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="Type of operation",
    )
    operation_details: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Operation-specific details",
    )

    # Result information
    result_status: Mapped[AuditOperationStatus] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="Operation result status",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if failed",
    )

    # Performance tracking
    duration_ms: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Operation duration in milliseconds",
    )

    # Timestamps (WHEN)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="Operation timestamp",
    )

    # Partitioning key
    partition_date: Mapped[date] = mapped_column(
        Date,
        server_default=func.current_date(),
        nullable=False,
        comment="Partition key for table partitioning",
    )

    # Relationships
    server: Mapped[Optional["MCPServerDB"]] = relationship(
        "MCPServerDB",
        back_populates="operations",
    )

    # Table indexes
    __table_args__ = (
        # Server-specific audit queries
        Index(
            "idx_audit_server_ops_server",
            "server_id",
        ),
        # Operation type queries
        Index(
            "idx_audit_server_ops_type",
            "operation_type",
            "executed_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditServerOperationDB("
            f"operation={self.operation_type.value}, "
            f"status={self.result_status.value}, "
            f"duration_ms={self.duration_ms:.2f}"
            f")>"
        )


class AuditCorrelation(Base):
    """Correlation ID tracking model for distributed request tracing.

    Tracks correlation IDs to link related audit events across different
    audit tables for complete request tracing.

    Attributes:
        correlation_id: Correlation ID for request tracing
        purpose: Business purpose/intent of the request
        request_context: Context about the request origin
        correlation_metadata: Additional correlation context (JSONB)
        created_at: Correlation creation timestamp

    Example:
        >>> correlation = AuditCorrelation(
        ...     correlation_id="corr-xyz-789",
        ...     purpose="Weather data retrieval",
        ... )
    """

    __tablename__ = "audit_correlations"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique identifier",
    )

    # Correlation identifier
    correlation_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="Correlation ID for request tracing",
    )

    # Context information (WHY/WHAT)
    purpose: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Business purpose/intent of the request",
    )
    request_context: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Context about the request origin",
    )
    correlation_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        server_default="{}",
        nullable=True,
        comment="Additional correlation context",
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Correlation creation timestamp",
    )

    def __repr__(self) -> str:
        return f"<AuditCorrelation(correlation_id={self.correlation_id})>"