"""Unit tests for PackCatalog (backend/browser_act_packs/catalog.py)."""

import logging

from backend.browser_act_packs.catalog import PackCatalog, PackInfo

# ── scanning the real vendored tree ─────────────────────────────────────────

def test_scans_vendored_packs_returns_plausible_count():
    """The vendored solutions/** tree (see VENDOR.md) has ~78 SKILL.md packs
    at vendor time. Don't hardcode the exact number (upstream may add/remove
    packs on refresh) — just assert it's a plausible non-trivial count."""
    catalog = PackCatalog()
    packs = catalog.list_packs()
    assert len(packs) >= 20, f"expected >= 20 vendored packs, got {len(packs)}"


def test_all_scanned_packs_are_pack_info_with_required_fields():
    catalog = PackCatalog()
    for pack in catalog.list_packs():
        assert isinstance(pack, PackInfo)
        assert pack.name
        assert pack.domain
        assert pack.capability
        assert pack.category
        assert pack.path


# ── get_pack / get_pack_by_name against a known vendored pack ──────────────

def test_get_pack_by_name_returns_known_pack():
    catalog = PackCatalog()
    pack = catalog.get_pack_by_name("taobao-keyword-search")
    assert pack is not None
    assert pack.domain == "ecommerce"
    assert pack.capability == "taobao-keyword-search"


def test_get_pack_by_domain_and_capability_returns_known_pack():
    catalog = PackCatalog()
    pack = catalog.get_pack("ecommerce", "taobao-keyword-search")
    assert pack is not None
    assert pack.name == "taobao-keyword-search"


def test_get_pack_unknown_returns_none():
    catalog = PackCatalog()
    assert catalog.get_pack("nonexistent-domain", "nonexistent-capability") is None


def test_get_pack_by_name_unknown_returns_none():
    catalog = PackCatalog()
    assert catalog.get_pack_by_name("this-pack-does-not-exist") is None


# ── skipping malformed packs ─────────────────────────────────────────────────

def test_pack_with_missing_frontmatter_is_skipped_with_warning(tmp_path, caplog):
    """A SKILL.md with no frontmatter block at all (no leading '---') has no
    'name' to key off of — it must be skipped, not crash the scan."""
    pack_dir = tmp_path / "some-category" / "broken-pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "SKILL.md").write_text("# Just a heading, no frontmatter\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="backend.browser_act_packs.catalog"):
        catalog = PackCatalog(root=tmp_path)

    assert catalog.list_packs() == []
    assert any("missing/unparseable frontmatter" in r.message for r in caplog.records)


def test_pack_with_frontmatter_missing_name_is_skipped(tmp_path, caplog):
    """Frontmatter block exists but has no 'name' key — still not usable."""
    pack_dir = tmp_path / "some-category" / "no-name-pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "SKILL.md").write_text(
        '---\ndescription: "has a description but no name"\n---\n\n# Body\n',
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="backend.browser_act_packs.catalog"):
        catalog = PackCatalog(root=tmp_path)

    assert catalog.list_packs() == []
    assert any("missing/unparseable frontmatter" in r.message for r in caplog.records)


def test_good_pack_alongside_broken_pack_only_good_one_counted(tmp_path, caplog):
    good_dir = tmp_path / "cat" / "good-pack"
    good_dir.mkdir(parents=True)
    (good_dir / "SKILL.md").write_text(
        '---\nname: good-pack\ndescription: "a fine pack"\n---\n\n# Good\n',
        encoding="utf-8",
    )

    broken_dir = tmp_path / "cat" / "broken-pack"
    broken_dir.mkdir(parents=True)
    (broken_dir / "SKILL.md").write_text("no frontmatter here at all\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="backend.browser_act_packs.catalog"):
        catalog = PackCatalog(root=tmp_path)

    packs = catalog.list_packs()
    assert len(packs) == 1
    assert packs[0].name == "good-pack"
    assert packs[0].domain == "cat"
    assert packs[0].capability == "good-pack"
