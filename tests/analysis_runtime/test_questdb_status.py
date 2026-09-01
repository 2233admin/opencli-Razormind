import httpx

from backend.analysis_runtime import QuestDBAnalysisRuntime
from backend.config import Settings


async def test_disabled_runtime_reports_bounded_status_without_network_access() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    settings = Settings(
        questdb_analysis_runtime_enabled=False,
        questdb_analysis_runtime_url="http://operator:secret@questdb:9000",
        questdb_analysis_runtime_health_url="http://operator:secret@questdb:9003",
    )
    runtime = QuestDBAnalysisRuntime(settings, transport=httpx.MockTransport(handler))

    status = await runtime.get_status()

    assert status.model_dump(mode="json") == {
        "runtime": "questdb",
        "state": "disabled",
        "reason_code": "disabled_by_configuration",
    }
    assert requests == []


async def test_unreachable_runtime_reports_unavailable_without_exception_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "operator:secret could not connect to private endpoint",
            request=request,
        )

    settings = Settings(
        questdb_analysis_runtime_enabled=True,
        questdb_analysis_runtime_url="http://operator:secret@questdb:9000",
        questdb_analysis_runtime_health_url="http://operator:secret@questdb:9003",
    )
    runtime = QuestDBAnalysisRuntime(settings, transport=httpx.MockTransport(handler))

    status = await runtime.get_status()

    assert status.model_dump(mode="json") == {
        "runtime": "questdb",
        "state": "unavailable",
        "reason_code": "connection_failed",
    }


async def test_failed_health_response_reports_unhealthy_without_response_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            text="SELECT secret FROM credentials at http://questdb:9000",
            request=request,
        )

    settings = Settings(
        questdb_analysis_runtime_enabled=True,
        questdb_analysis_runtime_url="http://questdb:9000",
        questdb_analysis_runtime_health_url="http://questdb:9003",
    )
    runtime = QuestDBAnalysisRuntime(settings, transport=httpx.MockTransport(handler))

    status = await runtime.get_status()

    assert status.model_dump(mode="json") == {
        "runtime": "questdb",
        "state": "unhealthy",
        "reason_code": "health_check_failed",
    }


async def test_failed_readiness_response_reports_unhealthy_with_fixed_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.port == 9003:
            return httpx.Response(200, text="Status: Healthy", request=request)
        return httpx.Response(200, json={"dataset": []}, request=request)

    settings = Settings(
        questdb_analysis_runtime_enabled=True,
        questdb_analysis_runtime_url="http://questdb:9000",
        questdb_analysis_runtime_health_url="http://questdb:9003",
    )
    runtime = QuestDBAnalysisRuntime(settings, transport=httpx.MockTransport(handler))

    status = await runtime.get_status()

    assert status.model_dump(mode="json") == {
        "runtime": "questdb",
        "state": "unhealthy",
        "reason_code": "readiness_check_failed",
    }
    assert len(requests) == 2
    assert requests[1].url.path == "/api/v1/sql/execute"
    assert requests[1].url.params["query"] == "select 1 as ready"


async def test_healthy_runtime_reports_ready() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 9003:
            return httpx.Response(200, text="Status: Healthy", request=request)
        return httpx.Response(
            200,
            json={
                "columns": [{"name": "ready", "type": "INT"}],
                "timestamp": -1,
                "dataset": [[1]],
                "count": 1,
            },
            request=request,
        )

    settings = Settings(
        questdb_analysis_runtime_enabled=True,
        questdb_analysis_runtime_url="http://questdb:9000",
        questdb_analysis_runtime_health_url="http://questdb:9003",
    )
    runtime = QuestDBAnalysisRuntime(settings, transport=httpx.MockTransport(handler))

    status = await runtime.get_status()

    assert status.model_dump(mode="json") == {
        "runtime": "questdb",
        "state": "ready",
        "reason_code": "ready",
    }


async def test_unreachable_query_port_reports_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 9003:
            return httpx.Response(200, text="Status: Healthy", request=request)
        raise httpx.ConnectError(
            "private query endpoint unavailable for SELECT 1",
            request=request,
        )

    settings = Settings(
        questdb_analysis_runtime_enabled=True,
        questdb_analysis_runtime_url="http://questdb:9000",
        questdb_analysis_runtime_health_url="http://questdb:9003",
    )
    runtime = QuestDBAnalysisRuntime(settings, transport=httpx.MockTransport(handler))

    status = await runtime.get_status()

    assert status.model_dump(mode="json") == {
        "runtime": "questdb",
        "state": "unavailable",
        "reason_code": "connection_failed",
    }


async def test_invalid_health_endpoint_reports_unavailable() -> None:
    settings = Settings(
        questdb_analysis_runtime_enabled=True,
        questdb_analysis_runtime_url="http://questdb:9000",
        questdb_analysis_runtime_health_url="http://[::1",
    )
    runtime = QuestDBAnalysisRuntime(settings)

    status = await runtime.get_status()

    assert status.model_dump(mode="json") == {
        "runtime": "questdb",
        "state": "unavailable",
        "reason_code": "connection_failed",
    }
