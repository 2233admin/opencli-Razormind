"""Integration tests for Browser Act integration PR-D seed manifests.

Exercises the two hand-authored ``channel.manifest.json`` seeds
(``search-research/google-search-serp``, ``ecommerce/taobao-keyword-search``)
against the REAL vendored pack tree (``PackCatalog()`` at its default root --
no synthetic ``tmp_path`` pack like PR-C's unit tests use) and the REAL pack
scripts (``scripts/serp-extract.py``, ``scripts/search-products.py`` run for
real via ``run_pack_script``, PR-C's second subprocess hop). Only the
browser-act eval hop -- the one that would need an actual browser/DOM -- is
mocked, at the same seam PR-C's ``tests/unit/channels/
test_browser_act_channel.py`` patches: ``backend.browser_act.cli._run``. This
proves the two manifests authored in this PR are wired correctly against the
real vendored files, not a stand-in.
"""

import json

import pytest
from unittest.mock import patch

from backend.browser_act.cli import BrowserActResult
from backend.browser_act_packs.catalog import PackCatalog
from backend.browser_act_packs.manifest import PackManifest, load_manifest
from backend.channels.browser_act_channel import BrowserActChannel

GOOGLE_PACK = "search-research/google-search-serp"
TAOBAO_PACK = "ecommerce/taobao-keyword-search"


def _run_side_effect(eval_responses: list[str]):
    """Same seam/shape as PR-C's test_browser_act_channel.py helper: answer
    "eval" subcommands from eval_responses in order (repeating the last one
    once exhausted), no-op every other subcommand (navigate/wait/click/input).
    """
    responses = list(eval_responses)
    state = {"i": 0}

    async def _side_effect(args, *, timeout=None, env=None):
        subcommand = args[2] if len(args) > 2 else None
        if subcommand == "eval":
            i = min(state["i"], len(responses) - 1)
            state["i"] += 1
            return BrowserActResult(returncode=0, stdout=responses[i], stderr="")
        return BrowserActResult(returncode=0, stdout="", stderr="")

    return _side_effect


@pytest.fixture
def channel():
    # Real vendored-packs root (no tmp_path override) -- these tests exercise
    # the actual PR-D manifests + actual PR-A/scripts.py files on disk.
    return BrowserActChannel(catalog=PackCatalog())


# ── (a) both seed manifests load + validate ──────────────────────────────


def test_google_seed_manifest_loads_and_validates():
    manifest_path = (
        PackCatalog().root
        / "search-research"
        / "google-search-serp"
        / "channel.manifest.json"
    )
    manifest = load_manifest(manifest_path)
    assert isinstance(manifest, PackManifest)
    assert manifest.domain == "search-research"
    assert manifest.capability == "google-search-serp"


def test_taobao_seed_manifest_loads_and_validates():
    manifest_path = (
        PackCatalog().root
        / "ecommerce"
        / "taobao-keyword-search"
        / "channel.manifest.json"
    )
    manifest = load_manifest(manifest_path)
    assert isinstance(manifest, PackManifest)
    assert manifest.domain == "ecommerce"
    assert manifest.capability == "taobao-keyword-search"


@pytest.mark.asyncio
async def test_google_seed_validate_config_valid(channel):
    errors = await channel.validate_config({"pack": GOOGLE_PACK, "params": {"query": "x"}})
    assert errors == []


@pytest.mark.asyncio
async def test_taobao_seed_validate_config_valid(channel):
    errors = await channel.validate_config(
        {"pack": TAOBAO_PACK, "params": {"keyword": "x"}}
    )
    assert errors == []


# ── (b) google seed happy path (single page, mode "none") ────────────────


@pytest.mark.asyncio
async def test_google_seed_happy_path_single_page(channel):
    serp = {
        "searchQuery": {"term": "machine learning"},
        "organicResults": [
            {"position": 1, "title": "A", "url": "http://a"},
            {"position": 2, "title": "B", "url": "http://b"},
        ],
        "paidResults": [],
        "relatedQueries": [],
    }
    with patch(
        "backend.browser_act.cli._run",
        side_effect=_run_side_effect([json.dumps(serp)]),
    ):
        result = await channel.collect(
            {"pack": GOOGLE_PACK, "params": {"query": "machine learning"}}, {}
        )

    assert result.success is True
    # The real serp-extract.py JS emits one dict (the whole SERP), so
    # collect() wraps it as a single item -- not one item per organic result.
    assert len(result.items) == 1
    assert len(result.items[0]["organicResults"]) == 2
    assert result.metadata["pages_fetched"] == 1


# ── (c) taobao seed pagination + stop_when (multi page) ──────────────────


@pytest.mark.asyncio
async def test_taobao_seed_paginates_and_stops_on_stop_when(channel):
    page1 = [{"itemId": str(n), "title": f"p{n}"} for n in range(12)]
    page2 = [{"itemId": str(n), "title": f"p{n}"} for n in range(5)]

    with patch(
        "backend.browser_act.cli._run",
        side_effect=_run_side_effect([json.dumps(page1), json.dumps(page2)]),
    ) as mock_run:
        result = await channel.collect(
            {"pack": TAOBAO_PACK, "params": {"keyword": "耳机"}}, {}
        )

    eval_calls = [
        call
        for call in mock_run.call_args_list
        if len(call.args[0]) > 2 and call.args[0][2] == "eval"
    ]
    # page1 has 12 items (>= 10) -> pagination continues; page2 has 5 items
    # (< 10) -> "result_count < 10" stop_when fires after that page.
    assert len(eval_calls) == 2
    assert result.success is True
    assert len(result.items) == 17
    assert result.metadata["pages_fetched"] == 2


# ── (d) success.min_count not met -> failure ──────────────────────────────


@pytest.mark.asyncio
async def test_taobao_seed_empty_page_fails_min_count(channel):
    with patch(
        "backend.browser_act.cli._run",
        side_effect=_run_side_effect([json.dumps([])]),
    ):
        result = await channel.collect(
            {"pack": TAOBAO_PACK, "params": {"keyword": "耳机"}}, {}
        )

    assert result.success is False
    assert result.error_type == "error"
    assert "min_count" in result.error


# ── (e) needs_human still works with a real seed ──────────────────────────


@pytest.mark.asyncio
async def test_google_seed_needs_human_on_captcha(channel):
    with patch(
        "backend.browser_act.cli._run",
        side_effect=_run_side_effect(
            [json.dumps({"error": True, "message": "captcha required"})]
        ),
    ):
        result = await channel.collect(
            {"pack": GOOGLE_PACK, "params": {"query": "machine learning"}}, {}
        )

    assert result.success is False
    assert result.error_type == "needs_human"
