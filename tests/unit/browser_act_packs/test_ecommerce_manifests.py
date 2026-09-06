"""Contract checks for executable e-commerce Browser Act packs."""

from pathlib import Path

import pytest

from backend.browser_act_packs.catalog import PackCatalog
from backend.browser_act_packs.manifest import load_manifest

_PACKS = {
    "ecommerce-listing": ("scripts/extract-listing.py", "url"),
    "ecommerce-product-detail": ("scripts/extract-product.py", "name"),
    "ecommerce-reviews": ("scripts/extract-reviews.py", "body"),
    "ecommerce-seller-info": ("scripts/extract-seller.py", "name"),
    "goofish-item-detail": ("scripts/extract-item-detail.py", "item_id"),
    "goofish-search-list": ("scripts/extract-search-items.py", "item_id"),
    "taobao-product-detail": ("scripts/extract-product.py", "title"),
    "taobao-product-reviews": ("scripts/extract-reviews.py", "username"),
    "taobao-shop-catalog": ("scripts/extract-catalog.py", "itemId"),
}


@pytest.mark.parametrize("capability, expected", sorted(_PACKS.items()))
def test_ecommerce_pack_manifest_is_executable(
    capability: str, expected: tuple[str, str]
) -> None:
    script_name, required_field = expected
    pack_dir = PackCatalog().root / "ecommerce" / capability
    manifest = load_manifest(pack_dir / "channel.manifest.json")

    assert manifest.domain == "ecommerce"
    assert manifest.capability == capability
    assert manifest.success.required_field == required_field
    eval_steps = [step for step in manifest.steps if step.op == "eval_script"]
    assert len(eval_steps) == 1
    assert eval_steps[0].script == script_name
    assert (pack_dir / script_name).is_file()


def test_ecommerce_manifests_use_only_supported_step_operations() -> None:
    supported = {"navigate", "wait", "eval_script", "click", "input"}
    for capability in _PACKS:
        manifest_path = (
            Path(PackCatalog().root)
            / "ecommerce"
            / capability
            / "channel.manifest.json"
        )
        manifest = load_manifest(manifest_path)
        assert {step.op for step in manifest.steps} <= supported
