import hashlib

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class BrowserBinding(TimestampMixin):
    """Maps an opencli site to a specific Chrome CDP endpoint."""

    __tablename__ = "browser_bindings"
    __table_args__ = (UniqueConstraint("site", name="uq_browser_bindings_site"),)

    browser_endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    site: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class BrowserRuntimeBundle(TimestampMixin):
    """Immutable, versioned manifest selecting browser runtime capabilities."""

    __tablename__ = "browser_runtime_bundles"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_browser_runtime_bundle_version"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    trust_level: Mapped[str] = mapped_column(String(30), nullable=False, default="trusted")
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="local")


class BrowserInstance(TimestampMixin):
    """Persistent desired configuration for a single browser runtime slot."""

    __tablename__ = "browser_instances"
    __table_args__ = (UniqueConstraint("profile_name", name="uq_browser_instances_profile_name"),)

    endpoint: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # "bridge" → opencli daemon+extension; "cdp" → direct CDP.
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="bridge")
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    agent_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_protocol: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Profile contains only login and site state. Runtime components are never
    # derived from this writable volume.
    profile_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="authenticated")
    profile_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    login_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    runtime_bundle_id: Mapped[str | None] = mapped_column(
        ForeignKey("browser_runtime_bundles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    resource_class: Mapped[str] = mapped_column(String(100), nullable=False, default="standard")
    startup_pages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    network_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


@event.listens_for(BrowserInstance, "before_insert")
def _default_profile_name(_mapper, _connection, target: BrowserInstance) -> None:
    if not target.profile_name:
        if len(target.endpoint) <= 100:
            target.profile_name = target.endpoint
        else:
            target.profile_name = f"endpoint-{hashlib.sha256(target.endpoint.encode()).hexdigest()[:64]}"


class BrowserRuntimeDeployment(TimestampMixin):
    """Loaded runtime fact reported by a slot; never a desired-state projection."""

    __tablename__ = "browser_runtime_deployments"
    __table_args__ = (
        UniqueConstraint("browser_instance_id", name="uq_browser_runtime_deployment_slot"),
    )

    browser_instance_id: Mapped[str] = mapped_column(
        ForeignKey("browser_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    loaded_bundle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    loaded_bundle_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    loaded_components: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    self_check: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="DEGRADED")
    diagnostics: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class BrowserCapabilityInvocation(TimestampMixin):
    """Auditable structured capability call with the complete runtime lineage."""

    __tablename__ = "browser_capability_invocations"

    browser_instance_id: Mapped[str] = mapped_column(
        ForeignKey("browser_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    capability: Mapped[str] = mapped_column(String(255), nullable=False)
    desired_bundle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    desired_bundle_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    loaded_bundle_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    component_versions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    page_before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    page_after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    risk: Mapped[str] = mapped_column(String(20), nullable=False)
    gate: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
