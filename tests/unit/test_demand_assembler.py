"""Unit tests for OpenCLI-catalog-aware source-slot matching in demand_assembler.

These tests mock the catalog boundary (``_load_opencli_catalog``, exactly the
seam the rest of the test suite already patches for opencli adapter tests)
and never invoke the real ``opencli`` CLI.
"""

from __future__ import annotations

from typing import Any

from backend.workflow.demand_assembler import (
    _catalog_slots_for_need,
    _legacy_keyword_slots_for_need,
    _source_slots_for_need,
)

_CATALOG_PATCH_TARGET = "backend.workflow.opencli_adapter_nodes._load_opencli_catalog"


def _catalog_entry(
    site: str,
    name: str = "search",
    *,
    description: str = "",
    access: str = "read",
) -> dict[str, Any]:
    return {
        "site": site,
        "name": name,
        "description": description,
        "access": access,
        "browser": False,
        "args": [],
        "columns": [],
    }


def _patch_catalog(monkeypatch, entries: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(_CATALOG_PATCH_TARGET, lambda: tuple(entries))


def _patch_catalog_raises(monkeypatch, exc: Exception) -> None:
    def _raise() -> tuple[dict[str, Any], ...]:
        raise exc

    monkeypatch.setattr(_CATALOG_PATCH_TARGET, _raise)


def test_catalog_slots_hit_by_exact_site_name_token(monkeypatch):
    _patch_catalog(
        monkeypatch,
        [_catalog_entry("acmenews", "headlines", description="Breaking news wire")],
    )

    slots = _catalog_slots_for_need("抓 acmenews 热门内容")

    assert slots == [
        {
            "id": "acmenews",
            "label": "Acmenews Headlines",
            "sourceGroup": "opencli",
            "site": "acmenews",
            "command": "headlines",
            "args": {"keyword": "acmenews"},
        }
    ]


def test_catalog_slots_hit_by_chinese_alias(monkeypatch):
    _patch_catalog(
        monkeypatch,
        [_catalog_entry("xiaohongshu", "search", description="Xiaohongshu search endpoint")],
    )

    slots = _catalog_slots_for_need("抓 小红书 热门内容")

    assert slots == [
        {
            "id": "xiaohongshu",
            "label": "Xiaohongshu Search",
            "sourceGroup": "opencli",
            "site": "xiaohongshu",
            "command": "search",
            "args": {"keyword": "热门"},
        }
    ]


def test_catalog_slots_hit_by_description_keyword(monkeypatch):
    _patch_catalog(
        monkeypatch,
        [_catalog_entry("widgetco", "fetch", description="gadget marketplace listings")],
    )

    slots = _catalog_slots_for_need("抓 gadget 热门内容")

    assert slots == [
        {
            "id": "widgetco",
            "label": "Widgetco Fetch",
            "sourceGroup": "opencli",
            "site": "widgetco",
            "command": "fetch",
            "args": {"keyword": "gadget"},
        }
    ]


def test_catalog_slots_excludes_write_only_adapters(monkeypatch):
    _patch_catalog(
        monkeypatch,
        [_catalog_entry("acmenews", "publish", description="acmenews publish", access="write")],
    )

    assert _catalog_slots_for_need("抓 acmenews 热门内容") == []


def test_no_match_returns_empty_from_catalog_and_legacy(monkeypatch):
    _patch_catalog(
        monkeypatch,
        [_catalog_entry("widgetco", "fetch", description="gadget marketplace listings")],
    )

    text = "帮我盯着未知平台的更新"

    assert _catalog_slots_for_need(text) == []
    assert _legacy_keyword_slots_for_need(text) == []
    assert _source_slots_for_need(text) == []


def test_catalog_load_raises_falls_back_to_legacy_keywords(monkeypatch):
    _patch_catalog_raises(monkeypatch, TypeError("'NoneType' object is not iterable"))

    text = "抓小红书热帖"

    assert _catalog_slots_for_need(text) == []
    assert _source_slots_for_need(text) == _legacy_keyword_slots_for_need(text)
    assert _source_slots_for_need(text) == [
        {
            "id": "xiaohongshu",
            "label": "Xiaohongshu Search",
            "sourceGroup": "social",
            "site": "xiaohongshu",
            "command": "search",
            "args": {"keyword": "热门"},
        }
    ]


def test_catalog_load_empty_falls_back_to_legacy_keywords_for_bilibili(monkeypatch):
    _patch_catalog(monkeypatch, [])

    text = "看下B站AI相关的热门帖子"

    assert _source_slots_for_need(text) == _legacy_keyword_slots_for_need(text)
    assert [slot["site"] for slot in _source_slots_for_need(text)] == ["bilibili"]


def test_catalog_slot_cap_matches_native_merge_arity(monkeypatch):
    _patch_catalog(
        monkeypatch,
        [
            _catalog_entry("sitea", "search", description="alpha site"),
            _catalog_entry("siteb", "search", description="beta site"),
            _catalog_entry("sitec", "search", description="gamma site"),
            _catalog_entry("sited", "search", description="delta site"),
        ],
    )

    slots = _catalog_slots_for_need("抓 sitea siteb sitec sited 热门内容")

    assert len(slots) == 2
    assert [slot["site"] for slot in slots] == ["sitea", "siteb"]


def test_catalog_match_tier_priority_beats_catalog_order(monkeypatch):
    # "alpha" sorts before "zylo" (list_opencli_adapter_nodes sorts by site),
    # but "zylo" is an exact-token (tier 0) hit while "alpha" only matches on
    # a description keyword (tier 2). Tier priority must win over both catalog
    # order and alphabetical order.
    _patch_catalog(
        monkeypatch,
        [
            _catalog_entry("alpha", "search", description="gadget reviews"),
            _catalog_entry("zylo", "search", description="unrelated stuff"),
        ],
    )

    slots = _catalog_slots_for_need("抓 zylo gadget 热门内容")

    assert [slot["site"] for slot in slots] == ["zylo", "alpha"]


def test_source_slots_for_need_prefers_catalog_match_when_available(monkeypatch):
    _patch_catalog(
        monkeypatch,
        [_catalog_entry("acmenews", "headlines", description="Breaking news wire")],
    )

    assert _source_slots_for_need("抓 acmenews 热门内容") == _catalog_slots_for_need(
        "抓 acmenews 热门内容"
    )


def test_legacy_keyword_slots_unchanged_for_both_known_sites(monkeypatch):
    # No monkeypatch needed: this exercises the pure legacy function directly,
    # which never touches the catalog.
    text = "抓小红书和B站AI热帖"

    slots = _legacy_keyword_slots_for_need(text)

    assert slots == [
        {
            "id": "xiaohongshu",
            "label": "Xiaohongshu Search",
            "sourceGroup": "social",
            "site": "xiaohongshu",
            "command": "search",
            "args": {"keyword": "AI"},
        },
        {
            "id": "bilibili",
            "label": "Bilibili Search",
            "sourceGroup": "video",
            "site": "bilibili",
            "command": "search",
            "args": {"keyword": "AI"},
        },
    ]


def test_keyword_excludes_platform_glue_and_downstream_processing(monkeypatch):
    text = "抓小红书和B站的AI热帖，清洗去重后保存"

    slots = _legacy_keyword_slots_for_need(text)

    assert [slot["args"] for slot in slots] == [
        {"keyword": "AI"},
        {"keyword": "AI"},
    ]


def test_known_site_aliases_ignore_unrelated_catalog_description_matches(monkeypatch):
    _patch_catalog(
        monkeypatch,
        [
            _catalog_entry("antigravity", "dump", description="AI workspace"),
            _catalog_entry("bilibili", "comments"),
            _catalog_entry("xiaohongshu", "comments"),
        ],
    )

    slots = _source_slots_for_need("抓小红书和B站AI热帖")

    assert [slot["site"] for slot in slots] == ["xiaohongshu", "bilibili"]
