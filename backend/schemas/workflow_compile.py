"""Stable compiler diagnostic contract shared by workflow schema groups."""

from pydantic import BaseModel, Field


class WorkflowCompileError(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None
    path: list[str] = Field(default_factory=list)
