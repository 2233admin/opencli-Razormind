from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.schemas.common import UTCModel

ChannelType = Literal[
    "opencli",
    "web_scraper",
    "api",
    "rss",
    "cli",
    "skill",
    "crawl4ai",
    "browser_act",
    "doubao_research",
    "douyin_detail",
    "feishu_table",
]


class DataSourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    channel_type: ChannelType
    channel_config: dict[str, Any] = Field(default_factory=dict)
    ai_config: dict[str, Any] | None = None
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)


class DataSourceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    channel_config: dict[str, Any] | None = None
    ai_config: dict[str, Any] | None = None
    enabled: bool | None = None
    tags: list[str] | None = None


class DataSourceRead(UTCModel):
    id: str
    name: str
    description: str | None
    channel_type: str
    channel_config: dict[str, Any]
    ai_config: dict[str, Any] | None
    enabled: bool
    tags: list[str]
    #: Raw stored per-source SourceObjective override (issue 02), null when
    #: none is set. This is the UNRESOLVED override dict — see
    #: backend.schemas.control.SourceControlStateRead.objective for what
    #: classification actually uses (resolved over defaults).
    objective_override: dict[str, Any] | None = None
    #: Issue 03 (Control Cycle + Actuator): set by an executed require_review
    #: action; a human clears it, the Control Cycle never does.
    review_required: bool = False
    #: Issue 03: set alongside enabled=False by an executed pause action;
    #: null once resumed (manually or by the Control Cycle's TTL auto-resume).
    paused_until: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DataSourceDetail(DataSourceRead):
    """Extended read with connectivity status."""
    connectivity_ok: bool | None = None
    connectivity_errors: list[str] = Field(default_factory=list)


class SourceObjectiveOverridePatch(BaseModel):
    """Body for ``PATCH /sources/{source_id}/objective`` (issue 02).

    ``objective_override`` is the ONLY field: a partial SourceObjective dict
    to store, or ``null`` to clear a previously-set override back to "use
    global defaults". Omitting the field entirely is also a no-op-safe
    clear-to-null under the model's default, but callers should always send
    it explicitly (set, update, or clear) — this endpoint is not a general
    source PATCH.

    Field-level validation happens downstream against
    ``backend.control.objectives.SourceObjectiveOverride`` (unknown keys or
    wrong types -> 422), not by this outer envelope, so a syntactically valid
    JSON object with a bad field name still gets a precise error.
    """

    objective_override: dict[str, Any] | None = None
