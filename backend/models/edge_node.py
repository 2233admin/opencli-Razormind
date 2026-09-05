from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class EdgeNode(TimestampMixin):
    """Remote agent node that has registered with the center.

    Tracks lifecycle (online / offline) and metadata for each edge agent.
    """

    __tablename__ = "edge_nodes"

    # Canonical URL the center uses to identify / reach this agent
    # (e.g. http://192.168.1.100:19823).  Acts as a unique logical key.
    url: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # "http" — center HTTP-POSTs to agent_url (LAN)
    # "ws"   — agent opened a reverse WS channel (NAT/firewall)
    protocol: Mapped[str] = mapped_column(String(10), nullable=False, default="http")
    # Chrome connection mode: "bridge" | "cdp" — how opencli connects to Chrome during collection
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="bridge")
    # Node startup/deployment type: "docker" | "shell"
    # Orthogonal to mode — both docker and shell nodes need Chrome (via bridge or cdp).
    node_type: Mapped[str] = mapped_column(String(20), nullable=False, default="docker")
    # "online" | "offline"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="offline")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Detected outbound IP at last registration
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # Agent-runtime types advertised at last WS register handshake (e.g. ["pi"]),
    # from backend.agent_runtimes.registry.available_runtimes(). NULL when the
    # node hasn't registered since this field was added, or registered over the
    # HTTP (non-WS) path, which doesn't carry runtime advertisement.
    runtimes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Per-runtime capability names measured by the edge adapter registry.
    runtime_capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class EdgeNodeEvent(TimestampMixin):
    """Append-only event log for each edge node (registered / online / offline)."""

    __tablename__ = "edge_node_events"

    node_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("edge_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "registered" | "online" | "offline"
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    event_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
