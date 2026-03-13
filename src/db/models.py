from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import (
    String,
    Text,
    DateTime,
    Enum as SQLEnum,
    Index,
    ForeignKey,
    Integer,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import enum


class MCPServerStatusEnum(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


class AuditActionEnum(str, enum.Enum):
    MOUNT = "MOUNT"
    UNMOUNT = "UNMOUNT"
    ENABLE = "ENABLE"
    DISABLE = "DISABLE"
    REMOVE = "REMOVE"


class Base(DeclarativeBase):
    pass


class MCPServer(Base):
    __tablename__ = "mcp_servers"

    server_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    spec_link: Mapped[str] = mapped_column(String(512), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    headers: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        SQLEnum(MCPServerStatusEnum),
        default=MCPServerStatusEnum.ACTIVE,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tags: Mapped[list["MCPServerTag"]] = relationship(
        "MCPServerTag",
        back_populates="server",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<MCPServer(server_name={self.server_name}, status={self.status})>"


class MCPServerTag(Base):
    __tablename__ = "mcp_server_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_name: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("mcp_servers.server_name", ondelete="CASCADE"),
        nullable=False,
    )
    tag_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        SQLEnum(MCPServerStatusEnum),
        default=MCPServerStatusEnum.ACTIVE,
        nullable=False,
    )

    # Relationships
    server: Mapped["MCPServer"] = relationship("MCPServer", back_populates="tags")

    # Unique constraint via index
    __table_args__ = (
        Index("idx_mcp_server_tags_unique", "server_name", "tag_name", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"<MCPServerTag(server_name={self.server_name}, tag_name={self.tag_name})>"
        )


class MCPAuditLog(Base):
    __tablename__ = "mcp_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tag_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(SQLEnum(AuditActionEnum), nullable=False)
    status_before: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status_after: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    performed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    __table_args__ = (Index("idx_mcp_audit_log_performed_at", "performed_at"),)

    def __repr__(self) -> str:
        return f"<MCPAuditLog(server_name={self.server_name}, action={self.action})>"
