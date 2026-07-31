"""PackCatalog — scans the vendored browser-act pack tree for SKILL.md files.

Domain/capability derivation (kept simple, per Browser Act integration decision #2/#5): the
SKILL.md frontmatter itself only declares ``name``/``description``, it does
not declare a domain/capability split. Rather than invent a second taxonomy,
this catalog derives domain/capability straight from the directory layout
that already exists (``<category>/<pack-name>/SKILL.md``):

- ``domain``     = the category directory (e.g. "ecommerce", "video-platforms")
- ``capability`` = the pack directory name (e.g. "taobao-keyword-search")

This is deliberately the simplest thing that works. If a future pack needs a
different domain/capability than its folder location implies, that's a
channel.manifest.json concern (PR-D+), not a reason to complicate this scan.

Packs with missing or unparseable frontmatter (no ``name`` key) are skipped
with a ``logging`` warning — never a crash. A pack directory with no
``SKILL.md`` at all is simply invisible to the catalog (there's nothing to
scan).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - pyyaml is a declared dependency
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False

logger = logging.getLogger(__name__)

#: Default scan root: this package's own directory, i.e.
#: backend/browser_act_packs/ (the vendored <category>/<pack-name>/ dirs live
#: right next to this file — see VENDOR.md).
PACKS_ROOT = Path(__file__).resolve().parent


class PackInfo(BaseModel):
    """One catalogued browser-act pack."""

    name: str
    description: str = ""
    category: str
    #: Path to the pack directory, relative to the catalog root, POSIX
    #: separators (e.g. "ecommerce/taobao-keyword-search").
    path: str
    domain: str
    capability: str


def _parse_frontmatter(text: str) -> Optional[dict]:
    """Parse the ``---``-delimited YAML frontmatter block at the top of a
    SKILL.md. Returns None when there is no well-formed frontmatter block."""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    raw = parts[1]
    if _HAS_YAML:
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError:
            return None
        return data if isinstance(data, dict) else None
    return _manual_frontmatter(raw)


def _manual_frontmatter(raw: str) -> Optional[dict]:
    """Minimal fallback frontmatter parser for the rare case pyyaml isn't
    importable. Handles flat ``key: value`` lines only (no nested YAML) —
    good enough for the ``name``/``description`` keys this catalog needs."""
    data: dict = {}
    key = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) and key is not None:
            data[key] = f"{data[key]} {line.strip()}".strip()
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip()
        data[key] = v.strip().strip('"').strip("'")
    return data or None


class PackCatalog:
    """Scans a browser_act_packs directory tree for ``<category>/<pack>/SKILL.md``
    files and builds an in-memory catalog. Read-only, built once at
    construction time — callers that need fresh data after packs change on
    disk should construct a new instance.
    """

    def __init__(self, root: Optional[Path | str] = None):
        self.root = Path(root) if root is not None else PACKS_ROOT
        self._packs: list[PackInfo] = self._scan()

    def _scan(self) -> list[PackInfo]:
        packs: list[PackInfo] = []
        for skill_md in sorted(self.root.rglob("SKILL.md")):
            rel = skill_md.relative_to(self.root)
            parts = rel.parts
            if len(parts) < 3:
                # Not <category>/<pack-name>/SKILL.md (e.g. a stray SKILL.md
                # sitting closer to the tree root) — not a pack we understand.
                logger.warning(
                    "Skipping SKILL.md at unexpected depth (expected "
                    "<category>/<pack>/SKILL.md): %s",
                    rel,
                )
                continue
            category, pack_name = parts[0], parts[1]
            try:
                # utf-8-sig strips a leading BOM when present (some vendored
                # SKILL.md files have one) and behaves like plain utf-8
                # otherwise — without this, a BOM'd file's `---` frontmatter
                # delimiter silently fails the startswith("---") check below.
                text = skill_md.read_text(encoding="utf-8-sig")
            except OSError as exc:
                logger.warning("Skipping unreadable SKILL.md %s: %s", skill_md, exc)
                continue
            frontmatter = _parse_frontmatter(text)
            if not frontmatter or not frontmatter.get("name"):
                logger.warning(
                    "Skipping pack with missing/unparseable frontmatter "
                    "(no 'name' key): %s",
                    skill_md,
                )
                continue
            packs.append(
                PackInfo(
                    name=str(frontmatter["name"]),
                    description=str(frontmatter.get("description", "") or ""),
                    category=category,
                    path=rel.parent.as_posix(),
                    domain=category,
                    capability=pack_name,
                )
            )
        return packs

    def list_packs(self) -> list[PackInfo]:
        return list(self._packs)

    def get_pack(self, domain: str, capability: str) -> Optional[PackInfo]:
        for pack in self._packs:
            if pack.domain == domain and pack.capability == capability:
                return pack
        return None

    def get_pack_by_name(self, name: str) -> Optional[PackInfo]:
        for pack in self._packs:
            if pack.name == name:
                return pack
        return None
