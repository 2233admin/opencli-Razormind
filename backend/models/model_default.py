from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class ModelDefault(TimestampMixin):
    """System default model candidates for one consumption role (model-provider runtime
    decision #4).

    One row per role (``role`` is UNIQUE): ``candidates`` is an ordered list
    of ``{"provider_id": ..., "model_id": ...}`` dicts — index 0 is the
    primary pick, the rest are failover order tried in sequence by the
    resolver (PR-D; this table only defines the shape, no resolve logic
    lives here). Roles map to the three consumption points model-provider runtime collapses
    onto ModelProvider (decision #4): ``chat`` (agent dock conversation),
    ``executor`` (skill_channel's cheap execution model), ``enrichment``
    (pipeline processor fallback).
    """

    __tablename__ = "model_defaults"

    #: chat | executor | enrichment — closed-set validated, see
    #: backend.llm.VALID_ROLES. unique=True: exactly one defaults row per role.
    role: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    #: Ordered [{"provider_id": ..., "model_id": ...}, ...]; index 0 = primary.
    candidates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
