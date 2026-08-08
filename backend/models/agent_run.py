"""Durable public execution records for interactive Agent runs."""

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import TimestampMixin


class AgentSession(TimestampMixin):
    __tablename__ = "agent_sessions"

    workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor_subject: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    runs: Mapped[list["AgentRun"]] = relationship(
        "AgentRun", back_populates="session", cascade="all, delete-orphan"
    )


class AgentRun(TimestampMixin):
    __tablename__ = "agent_runs"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="chat")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    request_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reply_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    session: Mapped[AgentSession] = relationship("AgentSession", back_populates="runs")
    events: Mapped[list["AgentRunEvent"]] = relationship(
        "AgentRunEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentRunEvent.sequence",
    )


class AgentRunEvent(TimestampMixin):
    __tablename__ = "agent_run_events"
    __table_args__ = (
        Index("ux_agent_run_events_run_id_sequence", "run_id", "sequence", unique=True),
    )

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[AgentRun] = relationship("AgentRun", back_populates="events")
