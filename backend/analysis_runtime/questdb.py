"""Narrow QuestDB capability adapter with a redacted public status."""

from enum import StrEnum
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from backend.config import Settings

_READINESS_QUERY = "select 1 as ready"


class RuntimeCapabilityState(StrEnum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    UNHEALTHY = "unhealthy"
    READY = "ready"


class RuntimeCapabilityReasonCode(StrEnum):
    DISABLED_BY_CONFIGURATION = "disabled_by_configuration"
    CONNECTION_FAILED = "connection_failed"
    HEALTH_CHECK_FAILED = "health_check_failed"
    READINESS_CHECK_FAILED = "readiness_check_failed"
    READY = "ready"


class RuntimeCapabilityStatus(BaseModel):
    """The complete public projection; transport details never cross this seam."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: Literal["questdb"] = "questdb"
    state: RuntimeCapabilityState
    reason_code: RuntimeCapabilityReasonCode


class QuestDBAnalysisRuntime:
    """Report whether the optional QuestDB analysis capability can be used."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def get_status(self) -> RuntimeCapabilityStatus:
        if not self._settings.questdb_analysis_runtime_enabled:
            return RuntimeCapabilityStatus(
                state=RuntimeCapabilityState.DISABLED,
                reason_code=RuntimeCapabilityReasonCode.DISABLED_BY_CONFIGURATION,
            )

        async with httpx.AsyncClient(
            timeout=self._settings.questdb_analysis_runtime_timeout_seconds,
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            try:
                health_response = await client.get(
                    self._settings.questdb_analysis_runtime_health_url
                )
            except (httpx.HTTPError, httpx.InvalidURL):
                return RuntimeCapabilityStatus(
                    state=RuntimeCapabilityState.UNAVAILABLE,
                    reason_code=RuntimeCapabilityReasonCode.CONNECTION_FAILED,
                )
            if (
                health_response.status_code != httpx.codes.OK
                or health_response.text.strip() != "Status: Healthy"
            ):
                return RuntimeCapabilityStatus(
                    state=RuntimeCapabilityState.UNHEALTHY,
                    reason_code=RuntimeCapabilityReasonCode.HEALTH_CHECK_FAILED,
                )
            try:
                readiness_response = await client.get(
                    f"{self._settings.questdb_analysis_runtime_url.rstrip('/')}"
                    "/api/v1/sql/execute",
                    params={"query": _READINESS_QUERY},
                )
            except (httpx.HTTPError, httpx.InvalidURL):
                return RuntimeCapabilityStatus(
                    state=RuntimeCapabilityState.UNAVAILABLE,
                    reason_code=RuntimeCapabilityReasonCode.CONNECTION_FAILED,
                )
            try:
                readiness_payload = readiness_response.json()
            except ValueError:
                readiness_payload = None
            if (
                readiness_response.status_code != httpx.codes.OK
                or not isinstance(readiness_payload, dict)
                or readiness_payload.get("dataset") != [[1]]
            ):
                return RuntimeCapabilityStatus(
                    state=RuntimeCapabilityState.UNHEALTHY,
                    reason_code=RuntimeCapabilityReasonCode.READINESS_CHECK_FAILED,
                )
            return RuntimeCapabilityStatus(
                state=RuntimeCapabilityState.READY,
                reason_code=RuntimeCapabilityReasonCode.READY,
            )
