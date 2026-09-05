"""Account metadata; credentials remain exclusively in the browser Profile."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class BrowserAccount(TimestampMixin):
    __tablename__ = "browser_accounts"

    platform: Mapped[str] = mapped_column(String(255), nullable=False)
    site_url: Mapped[str | None] = mapped_column(String(2048))
    login_target_id: Mapped[str | None] = mapped_column(String(100))
    operation_token: Mapped[str | None] = mapped_column(String(36))
    operation_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_clear: Mapped[bool | None] = mapped_column(Boolean)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    browser_instance_id: Mapped[str] = mapped_column(
        ForeignKey("browser_instances.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    profile_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unconfirmed")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String(255))
