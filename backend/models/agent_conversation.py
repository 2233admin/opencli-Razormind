from enum import StrEnum

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class AgentConversationStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class AgentConversationTurnStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PROPOSAL = "proposal"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class AgentConversation(TimestampMixin):
    """Durable Workspace-owned history for the Global Agent Dock."""

    __tablename__ = "agent_conversations"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'closed')", name="ck_agent_conversations_status"),
        CheckConstraint("revision >= 0", name="ck_agent_conversations_revision_nonnegative"),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=AgentConversationStatus.ACTIVE.value,
        server_default="active",
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    context_binding: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    execution_binding: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    agent_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AgentConversationTurn(TimestampMixin):
    """One immutable request/response boundary in an Agent conversation."""

    __tablename__ = "agent_conversation_turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_agent_conversation_turn_sequence"),
        UniqueConstraint(
            "conversation_id", "request_id", name="uq_agent_conversation_turn_request"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'proposal', 'failed', 'interrupted')",
            name="ck_agent_conversation_turns_status",
        ),
        CheckConstraint("sequence > 0", name="ck_agent_conversation_turns_sequence_positive"),
        CheckConstraint(
            "length(request_id) BETWEEN 1 AND 64", name="ck_agent_conversation_turn_request_length"
        ),
        CheckConstraint(
            "length(user_content) <= 20000", name="ck_agent_conversation_turns_content_length"
        ),
    )

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    active_slot: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    user_content: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    context_binding: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    tool_trace: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
