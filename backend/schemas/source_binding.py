from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import UTCModel


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100)
    adapter_type: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=4000)
    adapter_config: dict = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    status: str | None = None


class SourceRead(UTCModel):
    id: str
    workspace_id: str
    name: str
    slug: str
    adapter_type: str
    description: str | None
    status: str
    current_revision_number: int
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_config: dict = Field(default_factory=dict)


class SourceRevisionRead(UTCModel):
    id: str
    source_id: str
    revision_number: int
    adapter_config: dict
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceBindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100)
    source_revision_number: int = Field(ge=1)
    scope_config: dict = Field(default_factory=dict)


class SourceBindingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = None


class SourceBindingRead(UTCModel):
    id: str
    project_id: str
    source_id: str
    name: str
    slug: str
    status: str
    current_revision_number: int
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceBindingRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_revision_number: int = Field(ge=1)
    scope_config: dict = Field(default_factory=dict)


class SourceBindingRevisionRead(UTCModel):
    id: str
    source_binding_id: str
    revision_number: int
    pinned_source_revision_id: str
    scope_config: dict
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
