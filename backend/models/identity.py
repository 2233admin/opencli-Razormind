from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class WorkspaceRole(StrEnum):
    ADMIN = "admin"
    MAINTAINER = "maintainer"
    OPERATOR = "operator"
    VIEWER = "viewer"


class User(TimestampMixin):
    __tablename__ = "users"

    subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class LocalAdmin(TimestampMixin):
    __tablename__ = "local_admin_credentials"

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)


class Workspace(TimestampMixin):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WorkspaceMembership(TimestampMixin):
    __tablename__ = "workspace_memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(
            WorkspaceRole,
            name="workspace_role",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
    )


class Team(TimestampMixin):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)


class TeamMembership(TimestampMixin):
    __tablename__ = "team_memberships"
    __table_args__ = (UniqueConstraint("team_id", "user_id"),)

    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


class ServiceIdentity(TimestampMixin):
    __tablename__ = "service_identities"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
