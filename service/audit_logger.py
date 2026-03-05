"""Database audit logging service.

This module provides audit logging functionality that writes to the database
tables for comprehensive audit trails including tool invocations, server
operations, sessions, and correlation tracking.
"""

import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.audit import (
    AuditCorrelation,
    AuditSessionDB,
    AuditToolInvocationDB,
    AuditServerOperationDB,
    AuditOperationStatus,
    ServerOperationType,
)


class AuditLogger:
    """Database audit logging helper.
    
    Provides convenient methods for logging audit events to the database.
    All methods accept an AsyncSession and write audit records directly.
    
    Example:
        >>> audit_logger = AuditLogger()
        >>> await audit_logger.log_tool_invocation(
        ...     session=db_session,
        ...     tool_name="get_weather",
        ...     server_name="weather-api",
        ...     result_status=AuditOperationStatus.SUCCESS,
        ...     duration_ms=150.5,
        ... )
    """

    async def log_tool_invocation(
        self,
        session: AsyncSession,
        tool_name: str,
        server_name: Optional[str] = None,
        server_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        result_status: AuditOperationStatus = AuditOperationStatus.SUCCESS,
        result_summary: Optional[str] = None,
        duration_ms: float = 0.0,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        stack_trace: Optional[str] = None,
        tags: Optional[Dict[str, Any]] = None,
    ) -> AuditToolInvocationDB:
        """Log a tool invocation to the audit trail.
        
        Args:
            session: Database session for persistence
            tool_name: Name of the invoked tool
            server_name: Name of the source MCP server
            server_id: UUID of the source MCP server
            user_id: Authenticated user identifier
            correlation_id: Correlation ID for request tracing
            session_id: Session identifier for grouping
            arguments: Tool arguments (will be sanitized)
            result_status: Operation result status
            result_summary: Summary of the result
            duration_ms: Execution duration in milliseconds
            error_type: Type of error if failed
            error_message: Error message if failed
            stack_trace: Stack trace if failed
            tags: Additional metadata tags
            
        Returns:
            The created AuditToolInvocationDB record
        """
        invocation = AuditToolInvocationDB(
            correlation_id=correlation_id,
            session_id=session_id,
            server_id=server_id,
            tool_name=tool_name,
            arguments=arguments,
            result_status=result_status,
            result_summary=result_summary,
            duration_ms=duration_ms,
            error_type=error_type,
            error_message=error_message,
            stack_trace=stack_trace,
            tags=tags,
        )
        session.add(invocation)
        await session.flush()
        return invocation

    async def log_server_operation(
        self,
        session: AsyncSession,
        operation_type: ServerOperationType,
        server_name: str,
        server_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        operation_details: Optional[Dict[str, Any]] = None,
        result_status: AuditOperationStatus = AuditOperationStatus.SUCCESS,
        error_message: Optional[str] = None,
        duration_ms: float = 0.0,
    ) -> AuditServerOperationDB:
        """Log a server lifecycle operation to the audit trail.
        
        Args:
            session: Database session for persistence
            operation_type: Type of operation (MOUNT, UNMOUNT, ENABLE, DISABLE, REMOVE, UPDATE_CONFIG)
            server_name: Name of the target MCP server
            server_id: UUID of the target MCP server
            user_id: Authenticated user identifier
            correlation_id: Correlation ID for request tracing
            session_id: Session identifier for grouping
            operation_details: Operation-specific details
            result_status: Operation result status
            error_message: Error message if failed
            duration_ms: Operation duration in milliseconds
            
        Returns:
            The created AuditServerOperationDB record
        """
        operation = AuditServerOperationDB(
            correlation_id=correlation_id,
            session_id=session_id,
            server_id=server_id,
            operation_type=operation_type,
            operation_details=operation_details,
            result_status=result_status,
            error_message=error_message,
            duration_ms=duration_ms,
        )
        session.add(operation)
        await session.flush()
        return operation

    async def create_session(
        self,
        session: AsyncSession,
        session_id: str,
        user_id: Optional[str] = None,
        api_key_id: Optional[str] = None,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        session_metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditSessionDB:
        """Create a new audit session record.
        
        Sessions track user activity and group related audit events.
        
        Args:
            session: Database session for persistence
            session_id: Unique session identifier
            user_id: Authenticated user identifier
            api_key_id: API key used for authentication
            client_ip: Client IP address
            user_agent: Client user agent string
            session_metadata: Additional session context
            
        Returns:
            The created AuditSessionDB record
        """
        audit_session = AuditSessionDB(
            session_id=session_id,
            user_id=user_id,
            api_key_id=api_key_id,
            client_ip=client_ip,
            user_agent=user_agent,
            session_metadata=session_metadata or {},
        )
        session.add(audit_session)
        await session.flush()
        return audit_session

    async def end_session(
        self,
        session: AsyncSession,
        session_id: str,
    ) -> Optional[AuditSessionDB]:
        """Mark a session as ended.
        
        Args:
            session: Database session for persistence
            session_id: Session identifier to end
            
        Returns:
            The updated AuditSessionDB record or None if not found
        """
        from sqlalchemy import select
        
        result = await session.execute(
            select(AuditSessionDB).where(AuditSessionDB.session_id == session_id)
        )
        audit_session = result.scalar_one_or_none()
        
        if audit_session:
            audit_session.ended_at = datetime.utcnow()
            await session.flush()
        
        return audit_session

    async def create_correlation(
        self,
        session: AsyncSession,
        correlation_id: str,
        purpose: Optional[str] = None,
        request_context: Optional[str] = None,
        correlation_metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditCorrelation:
        """Create a new correlation ID record for distributed tracing.
        
        Correlation IDs link related audit events across different tables
        and services for complete request tracing.
        
        Args:
            session: Database session for persistence
            correlation_id: Unique correlation identifier
            purpose: Business purpose/intent of the request
            request_context: Context about the request origin
            correlation_metadata: Additional correlation context
            
        Returns:
            The created AuditCorrelation record
        """
        correlation = AuditCorrelation(
            correlation_id=correlation_id,
            purpose=purpose,
            request_context=request_context,
            correlation_metadata=correlation_metadata or {},
        )
        session.add(correlation)
        await session.flush()
        return correlation

    async def get_or_create_correlation(
        self,
        session: AsyncSession,
        correlation_id: str,
        purpose: Optional[str] = None,
        request_context: Optional[str] = None,
        correlation_metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditCorrelation:
        """Get existing correlation or create a new one.
        
        Args:
            session: Database session for persistence
            correlation_id: Unique correlation identifier
            purpose: Business purpose/intent of the request
            request_context: Context about the request origin
            correlation_metadata: Additional correlation context
            
        Returns:
            Existing or newly created AuditCorrelation record
        """
        from sqlalchemy import select
        
        result = await session.execute(
            select(AuditCorrelation).where(
                AuditCorrelation.correlation_id == correlation_id
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            return existing
        
        return await self.create_correlation(
            session=session,
            correlation_id=correlation_id,
            purpose=purpose,
            request_context=request_context,
            correlation_metadata=correlation_metadata,
        )

    async def log_tool_invocation_sync(
        self,
        session: AsyncSession,
        tool_name: str,
        server_id: Optional[uuid.UUID] = None,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        start_time: Optional[float] = None,
    ) -> Optional[AuditToolInvocationDB]:
        """Log a tool invocation with automatic duration calculation.
        
        Convenience method that calculates duration from start_time to now.
        
        Args:
            session: Database session for persistence
            tool_name: Name of the invoked tool
            server_id: UUID of the source MCP server
            correlation_id: Correlation ID for request tracing
            session_id: Session identifier for grouping
            arguments: Tool arguments (will be sanitized)
            start_time: Start time from time.time()
            
        Returns:
            The created AuditToolInvocationDB record or None if start_time not provided
        """
        if start_time is None:
            return None
            
        duration_ms = (time.time() - start_time) * 1000
        
        return await self.log_tool_invocation(
            session=session,
            tool_name=tool_name,
            server_id=server_id,
            correlation_id=correlation_id,
            session_id=session_id,
            arguments=arguments,
            duration_ms=duration_ms,
            result_status=AuditOperationStatus.SUCCESS,
        )


# Global audit logger instance for convenience
_default_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger instance.
    
    Returns:
        The global AuditLogger instance (creates one if needed)
    """
    global _default_logger
    if _default_logger is None:
        _default_logger = AuditLogger()
    return _default_logger
