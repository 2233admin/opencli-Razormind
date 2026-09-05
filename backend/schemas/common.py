from datetime import UTC, datetime

from pydantic import BaseModel


def _utc_isoformat(v: datetime) -> str:
    if v.tzinfo is None:
        v = v.replace(tzinfo=UTC)
    return v.astimezone(UTC).isoformat()


class UTCModel(BaseModel):
    """Base model that serializes datetimes as UTC ISO-8601 strings (with +00:00)."""

    model_config = {"json_encoders": {datetime: _utc_isoformat}}


class PaginationMeta(BaseModel):
    total: int
    page: int
    limit: int
    pages: int


class ApiResponse[T](BaseModel):
    success: bool
    data: T | None = None
    error: str | None = None
    meta: PaginationMeta | None = None

    @classmethod
    def ok(cls, data: T, meta: PaginationMeta | None = None) -> "ApiResponse[T]":
        return cls(success=True, data=data, meta=meta)

    @classmethod
    def fail(cls, error: str) -> "ApiResponse[None]":
        return cls(success=False, error=error)
