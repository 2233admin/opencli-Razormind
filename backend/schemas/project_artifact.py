"""Public contracts for persisted project artifacts.

The artifact body is intentionally opaque to this contract.  Native
intelligence stages own the payload schema; this module only adds the
project/run ownership envelope that makes an artifact safe to read from
Studio.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.schemas.common import UTCModel


class ProjectArtifactSummary(UTCModel):
    """A bounded artifact listing item without the persisted body."""

    id: str
    artifact_id: str
    title: str
    media_type: str
    kind: str
    content_hash: str
    workspace_id: str
    project_id: str
    workflow_id: str
    run_id: str
    session_id: str
    conversation_id: str | None = None
    source: str | None = None
    simulated: bool
    created_at: datetime
    updated_at: datetime


class ProjectArtifactDetail(ProjectArtifactSummary):
    """A summary plus the bounded body and persisted provenance envelope."""

    schema_version: str
    content: dict[str, Any]
    payload: dict[str, Any]
    provenance: dict[str, Any]
    grounding_artifact_ids: list[str]
    algorithm_version: str
    seed: int


__all__ = ["ProjectArtifactDetail", "ProjectArtifactSummary"]
