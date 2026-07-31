from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class ProviderModel(TimestampMixin):
    """One model in a provider's catalog (model-provider runtime decision #3).

    Populated either by discovery sync (``source="discovered"`` — OpenAI-compat
    ``GET {base_url}/v1/models``, or the Anthropic hardcoded catalog for
    ``provider_type="claude"``, both PR-B/C) or entered by hand
    (``source="manual"``). Sync is an upsert that must never overwrite or
    delete a ``manual`` row (PR-C concern; this table only defines the shape).

    ``provider_id`` is a real FK — unlike ``AIAgent.provider_id``, which stays
    a loose string column per model-provider runtime decision #9 (the migration cost isn't
    worth it there) — so deleting a ``ModelProvider`` cascades its whole
    catalog away instead of leaving orphan rows.
    """

    __tablename__ = "provider_models"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", name="uq_provider_models_provider_model"),
    )

    provider_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("model_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Provider-native model name (e.g. "claude-sonnet-5", "gpt-4o").
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    #: llm | embedding | rerank — v1 only ever writes "llm"; the column stays
    #: a plain string (closed-set validated at the Pydantic/backend.llm
    #: layer, see backend.llm.VALID_MODEL_TYPES) so embedding/rerank rows can
    #: land later without a migration.
    model_type: Mapped[str] = mapped_column(String(50), nullable=False, default="llm")
    #: e.g. {"tools": true, "vision": false, "context_window": 200000}.
    capabilities: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    #: discovered | manual — closed-set validated, see backend.llm.VALID_MODEL_SOURCES.
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
