from __future__ import annotations

from subprocess import CompletedProcess

import pytest

from backend.workflow import opencli_adapter_nodes


def test_opencli_catalog_fails_closed_when_decoding_produces_no_stdout(monkeypatch) -> None:
    monkeypatch.setattr(opencli_adapter_nodes, "resolve_opencli_bin", lambda: "opencli")
    monkeypatch.setattr(
        opencli_adapter_nodes.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args, 0, stdout=None, stderr=None),
    )
    opencli_adapter_nodes._load_opencli_catalog.cache_clear()
    try:
        assert opencli_adapter_nodes._load_opencli_catalog() == ()
    finally:
        opencli_adapter_nodes._load_opencli_catalog.cache_clear()


def _catalog_entry(**overrides) -> dict:
    return {
        "site": "example",
        "name": "list",
        "description": "Example public reader",
        "access": "read",
        "strategy": "public",
        "browser": False,
        "args": [],
        **overrides,
    }


def test_public_adapter_without_runtime_dependencies_remains_runnable() -> None:
    node = opencli_adapter_nodes._build_adapter_node(_catalog_entry())

    assert node.status == "runnable"
    assert node.runtimeReadiness == "source_slot_ready"
    assert node.manifest["canvas"]["runBlocked"] is False
    assert node.manifest["availability"] == {"available": True, "reason": None}


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        (
            _catalog_entry(site="sse", name="company-list"),
            "upstream_http_404",
        ),
        (
            _catalog_entry(site="browser-site", name="feed", browser=True),
            "browser_session_readiness_unverified",
        ),
        (
            _catalog_entry(site="cookie-site", name="feed", strategy="cookie"),
            "cookie_readiness_unverified",
        ),
    ],
)
def test_unverified_adapter_dependencies_fail_closed(entry: dict, reason: str) -> None:
    node = opencli_adapter_nodes._build_adapter_node(entry)

    assert node.status == "preview_only"
    assert node.manifest["canvas"]["runBlocked"] is True
    assert node.manifest["availability"] == {"available": False, "reason": reason}


def test_known_unavailable_adapter_cannot_materialize(monkeypatch) -> None:
    monkeypatch.setattr(
        opencli_adapter_nodes,
        "get_opencli_adapter_catalog",
        lambda **kwargs: (_catalog_entry(site="sse", name="company-list"),),
    )

    with pytest.raises(
        opencli_adapter_nodes.OpenCLIAdapterNodeMaterializationError,
        match="not currently runnable",
    ) as error:
        opencli_adapter_nodes.materialize_opencli_adapter_node(
            "opencli.adapter.sse.company-list"
        )

    assert error.value.code == "opencli_adapter_node_unavailable"
