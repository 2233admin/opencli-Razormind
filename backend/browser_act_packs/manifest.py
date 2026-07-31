"""PackManifest — the ``channel.manifest.json`` schema (Browser Act integration decision #5).

This is the machine-readable execution contract that ``BrowserActChannel``
(PR-C) interprets generically: ``steps`` is a sequence of browser-act
operations to run for one page of results, ``param_schema`` describes what
the caller must/may supply, ``pagination`` describes how to fetch subsequent
pages, and ``success`` describes when a result is trustworthy enough to
return. The channel is a generic interpreter of this manifest — it never has
per-pack code.

PR-A (this module) defines only the schema + a loader. No pack ships a
``channel.manifest.json`` yet; seeding 2-3 packs with real manifests is
PR-D. Field sets are deliberately permissive/optional per-op (see ``Step``)
since a single step in the sequence only uses a subset of fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ParamSpec(BaseModel):
    """One entry in a pack's ``param_schema`` — describes one caller-supplied
    parameter (e.g. "keyword", "page")."""

    name: str
    required: bool = False
    default: Optional[str] = None
    enum: Optional[list[str]] = None


#: The browser-act operations a manifest step can drive. See Browser Act integration decision
#: #1/#6: the channel drives these deterministically, argv-only, no shell.
StepOp = Literal["navigate", "wait", "eval_script", "click", "input"]


class Step(BaseModel):
    """One step in a manifest's ``steps`` sequence. Fields are optional and
    only the ones relevant to ``op`` are expected to be set by an author:

    - ``navigate``: ``url_template`` (e.g. "https://s.taobao.com/search?q={keyword}")
    - ``wait``: ``wait_mode`` (e.g. "stable")
    - ``eval_script``: ``script`` (path to a scripts/*.py in the pack dir,
      relative to the pack), ``args`` (argv template, e.g. ["{keyword}", "--page", "{page}"])
    - ``click`` / ``input``: ``selector`` or ``index`` to target an element;
      ``input`` additionally uses ``value``

    Extra/unused fields for a given ``op`` are simply left as ``None`` — this
    model does not enforce which fields go with which op (that validation, if
    ever needed, belongs to the PR-C interpreter, not the schema).
    """

    op: StepOp
    url_template: Optional[str] = None
    wait_mode: Optional[str] = None
    script: Optional[str] = None
    args: Optional[list[str]] = None
    selector: Optional[str] = None
    index: Optional[int] = None
    value: Optional[str] = None


class Pagination(BaseModel):
    """How to fetch subsequent pages. ``mode`` is free-form (e.g. "url_page",
    "none") — the PR-C interpreter switches on it; this schema doesn't
    enumerate every mode so new pagination styles don't require a schema
    change."""

    mode: str
    url_template: Optional[str] = None
    page_param: Optional[str] = None
    stop_when: Optional[str] = None


class SuccessCriteria(BaseModel):
    """When a collected batch counts as a successful result."""

    min_count: int = 1
    required_field: Optional[str] = None


class PackManifest(BaseModel):
    """The full ``channel.manifest.json`` contract for one pack."""

    domain: str
    capability: str
    param_schema: list[ParamSpec] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    pagination: Pagination
    success: SuccessCriteria = Field(default_factory=SuccessCriteria)


def load_manifest(path: str | Path) -> PackManifest:
    """Read and validate a ``channel.manifest.json`` file.

    Raises ``json.JSONDecodeError`` on malformed JSON and
    ``pydantic.ValidationError`` on a well-formed-but-invalid document —
    deliberately not swallowed here; PR-C's caller decides how those surface.
    """
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    return PackManifest.model_validate(data)
