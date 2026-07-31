"""GET /api/v1/browser-act/packs — read-only catalog listing for the
BrowserAct channel's frontend surfacing (Browser Act integration PR-E, decision #9).

Reuses ``PackCatalog`` (PR-A) + ``load_manifest`` (PR-A schema / PR-D seeds)
— no new catalog mechanism, no DB table. Read-only, no auth (matches the
admin-tool style of the other v1 routers, e.g. providers.py/presets.py).

Never exposes anything from ``SourceCredential``/``AuthManager``: this is a
static vendored-file catalog listing (name/description/category/domain/
capability/path/has_manifest/param_schema) — there is no per-source config
or credential in scope here at all, so there is nothing to leak.
"""

import logging
from json import JSONDecodeError
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ValidationError

from backend.browser_act_packs.catalog import PackCatalog
from backend.browser_act_packs.manifest import load_manifest
from backend.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/browser-act", tags=["browser-act"])


class BrowserActPackRead(BaseModel):
    """One catalogued pack + its manifest status, for the sources UI's
    preset dropdown (decision #9). ``param_schema`` is the manifest's own
    ``param_schema`` (``ParamSpec``-shaped dicts) when ``has_manifest`` is
    True, else empty — a missing/invalid manifest is a valid catalog entry,
    never a 500 (mirrors ``BrowserActChannel._load_pack_manifest``'s
    guard-don't-crash contract)."""

    name: str
    description: str = ""
    category: str
    domain: str
    capability: str
    path: str
    has_manifest: bool
    param_schema: list[dict[str, Any]] = []


@router.get("/packs", response_model=ApiResponse[list[BrowserActPackRead]])
async def list_packs() -> ApiResponse:
    """List every vendored browser-act pack the catalog can see, each
    annotated with whether it has a machine-readable ``channel.manifest.json``
    yet (PR-D seeded 2 of the ~78 vendored packs; the rest are catalog-only
    until a future manifest is authored for them)."""
    catalog = PackCatalog()
    packs: list[BrowserActPackRead] = []
    for info in catalog.list_packs():
        manifest_path = catalog.root / info.path / "channel.manifest.json"
        has_manifest = manifest_path.exists()
        param_schema: list[dict[str, Any]] = []
        if has_manifest:
            try:
                manifest = load_manifest(manifest_path)
                param_schema = [p.model_dump() for p in manifest.param_schema]
            except (JSONDecodeError, ValidationError) as exc:
                logger.warning(
                    "browser-act packs endpoint: pack %s has an invalid "
                    "channel.manifest.json, reporting has_manifest=false: %s",
                    info.path,
                    exc,
                )
                has_manifest = False
        packs.append(
            BrowserActPackRead(
                name=info.name,
                description=info.description,
                category=info.category,
                domain=info.domain,
                capability=info.capability,
                path=info.path,
                has_manifest=has_manifest,
                param_schema=param_schema,
            )
        )
    return ApiResponse.ok(packs)
