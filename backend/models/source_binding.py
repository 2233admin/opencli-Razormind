"""Workspace-owned Source/SourceRevision and Project-owned SourceBinding/SourceBindingRevision.

Per ADR-0041: Source is a reusable, Workspace-owned external endpoint; a Project
narrows it into a SourceBinding that scopes authorization and collection. Semantic
edits to either create a new immutable revision — a SourceBindingRevision always
pins an exact SourceRevision explicitly, it never silently follows the latest one.
Status changes (credential rotation, health, safety revocation) mutate the parent
row directly and do not require a new revision.

This is a distinct concept from the legacy, unscoped `DataSource`
(backend/models/source.py, table `data_sources`, exposed at /sources) and from
`FeedProvider` (backend/models/feed_provider.py, the multi-project "Data Feed").
Neither is touched or redefined by this module; both remain as-is.
"""

from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class SourceLifecycleStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


class Source(TimestampMixin):
    """Workspace-owned identity for a reusable external endpoint."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SourceLifecycleStatus] = mapped_column(
        Enum(
            SourceLifecycleStatus,
            name="source_lifecycle_status",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
        default=SourceLifecycleStatus.ACTIVE,
    )
    current_revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class SourceRevision(TimestampMixin):
    """Immutable snapshot of a Source's adapter/endpoint/connection config."""

    __tablename__ = "source_revisions"
    __table_args__ = (UniqueConstraint("source_id", "revision_number"),)

    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    adapter_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class SourceBinding(TimestampMixin):
    """Project-owned authorization + collection scope over a Workspace Source."""

    __tablename__ = "source_bindings"
    __table_args__ = (UniqueConstraint("project_id", "slug"),)

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[SourceLifecycleStatus] = mapped_column(
        Enum(
            SourceLifecycleStatus,
            name="source_binding_lifecycle_status",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
        default=SourceLifecycleStatus.ACTIVE,
    )
    current_revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class SourceBindingRevision(TimestampMixin):
    """Immutable pin of an exact SourceRevision plus the frozen scope it authorizes."""

    __tablename__ = "source_binding_revisions"
    __table_args__ = (UniqueConstraint("source_binding_id", "revision_number"),)

    source_binding_id: Mapped[str] = mapped_column(
        ForeignKey("source_bindings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pinned_source_revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    scope_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
