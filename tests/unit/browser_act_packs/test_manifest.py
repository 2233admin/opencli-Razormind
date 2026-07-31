"""Unit tests for PackManifest (backend/browser_act_packs/manifest.py) —
Browser Act integration decision #5 schema + loader. PR-A defines schema only, no seed content."""

import json

import pytest
from pydantic import ValidationError

from backend.browser_act_packs.manifest import PackManifest, load_manifest


def _well_formed_dict() -> dict:
    return {
        "domain": "ecommerce",
        "capability": "taobao-keyword-search",
        "param_schema": [
            {"name": "keyword", "required": True},
            {"name": "page", "required": False, "default": "1"},
            {"name": "sort", "required": False, "enum": ["", "sale-desc", "price-asc"]},
        ],
        "steps": [
            {"op": "navigate", "url_template": "https://s.taobao.com/search?q={keyword}"},
            {"op": "wait", "wait_mode": "stable"},
            {
                "op": "eval_script",
                "script": "scripts/search-products.py",
                "args": ["{keyword}", "--page", "{page}"],
            },
        ],
        "pagination": {
            "mode": "url_page",
            "url_template": "https://s.taobao.com/search?q={keyword}&page={page}",
            "page_param": "page",
            "stop_when": "result_count < 10",
        },
        "success": {"min_count": 1, "required_field": "itemId"},
    }


# ── validates well-formed dict ──────────────────────────────────────────────

def test_validates_well_formed_manifest():
    manifest = PackManifest.model_validate(_well_formed_dict())
    assert manifest.domain == "ecommerce"
    assert manifest.capability == "taobao-keyword-search"
    assert len(manifest.param_schema) == 3
    assert manifest.param_schema[0].name == "keyword"
    assert manifest.param_schema[0].required is True
    assert len(manifest.steps) == 3
    assert manifest.steps[0].op == "navigate"
    assert manifest.pagination.mode == "url_page"
    assert manifest.success.min_count == 1
    assert manifest.success.required_field == "itemId"


def test_defaults_when_optional_fields_omitted():
    data = _well_formed_dict()
    del data["param_schema"]
    del data["steps"]
    del data["success"]
    manifest = PackManifest.model_validate(data)
    assert manifest.param_schema == []
    assert manifest.steps == []
    assert manifest.success.min_count == 1
    assert manifest.success.required_field is None


# ── rejects bad dicts ────────────────────────────────────────────────────────

def test_rejects_bad_op_value():
    data = _well_formed_dict()
    data["steps"][0]["op"] = "teleport"  # not in the allowed StepOp Literal
    with pytest.raises(ValidationError):
        PackManifest.model_validate(data)


def test_rejects_missing_required_domain():
    data = _well_formed_dict()
    del data["domain"]
    with pytest.raises(ValidationError):
        PackManifest.model_validate(data)


def test_rejects_missing_required_pagination():
    data = _well_formed_dict()
    del data["pagination"]
    with pytest.raises(ValidationError):
        PackManifest.model_validate(data)


def test_rejects_non_dict_step():
    data = _well_formed_dict()
    data["steps"] = ["navigate"]
    with pytest.raises(ValidationError):
        PackManifest.model_validate(data)


# ── load_manifest(path) ──────────────────────────────────────────────────────

def test_load_manifest_reads_and_validates_json_file(tmp_path):
    manifest_path = tmp_path / "channel.manifest.json"
    manifest_path.write_text(json.dumps(_well_formed_dict()), encoding="utf-8")

    manifest = load_manifest(manifest_path)

    assert manifest.domain == "ecommerce"
    assert manifest.capability == "taobao-keyword-search"


def test_load_manifest_raises_on_bad_json(tmp_path):
    manifest_path = tmp_path / "channel.manifest.json"
    manifest_path.write_text("{ this is not valid json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        load_manifest(manifest_path)


def test_load_manifest_raises_on_invalid_schema(tmp_path):
    data = _well_formed_dict()
    del data["domain"]
    manifest_path = tmp_path / "channel.manifest.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_manifest(manifest_path)
