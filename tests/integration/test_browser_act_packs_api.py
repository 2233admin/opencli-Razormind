"""HTTP-seam tests for GET /api/v1/browser-act/packs (Browser Act integration PR-E, decision
#9): the real vendored catalog (backend/browser_act_packs/), no fixture
override -- this is a static file-tree scan, deterministic regardless of
test order/host, same as PackCatalog's own unit tests.
"""

import pytest

SEEDED_WITH_MANIFEST = {
    "ecommerce/taobao-keyword-search",
    "search-research/google-search-serp",
}


@pytest.mark.asyncio
async def test_list_packs_returns_ok_envelope(client):
    response = await client.get("/api/v1/browser-act/packs")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert isinstance(body["data"], list)


@pytest.mark.asyncio
async def test_list_packs_includes_vendored_packs(client):
    """PR-A vendored >=20 packs (78 actually, assertion deliberately not
    pinned to the exact upstream count -- see PackCatalog's own tests)."""
    response = await client.get("/api/v1/browser-act/packs")
    data = response.json()["data"]
    assert len(data) >= 20


@pytest.mark.asyncio
async def test_seeded_packs_have_manifest_and_nonempty_param_schema(client):
    response = await client.get("/api/v1/browser-act/packs")
    data = response.json()["data"]
    by_path = {p["path"]: p for p in data}

    for path in SEEDED_WITH_MANIFEST:
        assert path in by_path, f"seeded pack {path!r} missing from catalog"
        pack = by_path[path]
        assert pack["has_manifest"] is True
        assert isinstance(pack["param_schema"], list)
        assert len(pack["param_schema"]) > 0
        for spec in pack["param_schema"]:
            assert "name" in spec
            assert "required" in spec


@pytest.mark.asyncio
async def test_unseeded_pack_reports_has_manifest_false(client):
    response = await client.get("/api/v1/browser-act/packs")
    data = response.json()["data"]

    unseeded = [p for p in data if p["path"] not in SEEDED_WITH_MANIFEST]
    assert unseeded, "expected at least one pack without a channel.manifest.json"
    for pack in unseeded:
        assert pack["has_manifest"] is False
        assert pack["param_schema"] == []


@pytest.mark.asyncio
async def test_every_pack_declares_required_fields(client):
    response = await client.get("/api/v1/browser-act/packs")
    data = response.json()["data"]
    assert len(data) > 0
    for pack in data:
        assert pack["name"]
        assert pack["category"]
        assert pack["domain"]
        assert pack["capability"]
        assert pack["path"]
        assert isinstance(pack["has_manifest"], bool)


@pytest.mark.asyncio
async def test_response_never_carries_credential_fields(client):
    """The catalog listing is a static file-tree scan -- it must never carry
    anything from SourceCredential/AuthManager (no api_key/credential/secret
    field anywhere in the payload, seeded or not)."""
    response = await client.get("/api/v1/browser-act/packs")
    raw = response.text.lower()
    for forbidden in ("api_key", "credential", "secret", "ciphertext", "browser_act_api_key"):
        assert forbidden not in raw, f"packs response unexpectedly contains {forbidden!r}"
