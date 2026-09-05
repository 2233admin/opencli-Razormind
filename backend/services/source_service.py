import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.channels.registry import get_channel
from backend.control.objectives import SourceObjectiveOverride
from backend.models.source import DataSource
from backend.models.source_credential import SourceCredential
from backend.schemas.source import DataSourceCreate, DataSourceUpdate
from backend.security.url_guard import (
    SSRFValidationError,
    avalidate_public_url,
    guarded_async_client,
)

#: common feed paths probed when a site's <head> has no <link rel="alternate">
#: feed tags at all (many blogs/CMSes still serve a feed at one of these).
_COMMON_FEED_PATHS = ["/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml"]
_FEED_MIME_TYPES = {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}
_MAX_REMOTE_OPML_BYTES = 2 * 1024 * 1024


async def list_sources(
    session: AsyncSession,
    enabled: bool | None = None,
    channel_type: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[DataSource], int]:
    query = select(DataSource).order_by(DataSource.created_at.desc())
    count_query = select(func.count()).select_from(DataSource)

    if enabled is not None:
        query = query.where(DataSource.enabled == enabled)
        count_query = count_query.where(DataSource.enabled == enabled)
    if channel_type:
        query = query.where(DataSource.channel_type == channel_type)
        count_query = count_query.where(DataSource.channel_type == channel_type)

    total = (await session.execute(count_query)).scalar_one()
    offset = (page - 1) * limit
    result = await session.execute(query.offset(offset).limit(limit))
    return result.scalars().all(), total


async def get_source(session: AsyncSession, source_id: str) -> DataSource | None:
    result = await session.execute(
        select(DataSource).where(DataSource.id == source_id)
    )
    return result.scalar_one_or_none()


async def create_source(session: AsyncSession, data: DataSourceCreate) -> DataSource:
    source = DataSource(**data.model_dump())
    session.add(source)
    await session.flush()
    await session.refresh(source)
    return source


async def update_source(
    session: AsyncSession, source: DataSource, data: DataSourceUpdate
) -> DataSource:
    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(source, key, value)
    await session.flush()
    await session.refresh(source)
    return source


async def set_objective_override(
    session: AsyncSession, source: DataSource, override: dict[str, Any] | None
) -> DataSource:
    """Set, update, or clear (``override=None``) a source's per-source
    SourceObjective override (issue 02).

    Validates ``override`` against ``SourceObjectiveOverride`` (unknown
    field names / wrong types raise ``pydantic.ValidationError`` — the
    caller, ``backend.api.v1.sources.set_source_objective``, translates that
    into a 422). Only the fields the caller actually set are persisted
    (``exclude_none``), so a merge later via
    ``backend.control.objectives.resolve_objective`` sees exactly the
    overridden keys, nothing else.
    """
    if override is None:
        source.objective_override = None
    else:
        validated = SourceObjectiveOverride.model_validate(override)
        source.objective_override = validated.model_dump(exclude_none=True)
    await session.flush()
    await session.refresh(source)
    return source


async def delete_source(session: AsyncSession, source: DataSource) -> None:
    # source_credentials has no FK/cascade to data_sources (it's written by
    # AuthManager, a separate session, so a DB-level FK would need cross-module
    # coordination) — clean it up here instead, or the encrypted secrets for a
    # deleted source live forever.
    await session.execute(
        delete(SourceCredential).where(SourceCredential.source_id == source.id)
    )
    await session.delete(source)
    await session.flush()


async def validate_source_config(source: DataSource) -> list[str]:
    try:
        channel = get_channel(source.channel_type)
        return await channel.validate_config(source.channel_config)
    except ValueError as exc:
        return [str(exc)]


async def test_source_connectivity(source: DataSource) -> tuple[bool, list[str]]:
    errors = await validate_source_config(source)
    if errors:
        return False, errors
    try:
        channel = get_channel(source.channel_type)
        ok = await channel.health_check(source.channel_config, source.id)
        return ok, []
    except Exception as exc:
        return False, [str(exc)]


# ── RSS source onboarding: feed discovery + OPML bulk import ────────────────────
async def discover_feeds(url: str) -> list[dict[str, Any]]:
    """Given a site's homepage (not a feed URL itself), find candidate RSS/Atom
    feeds. Setup-time only — never called from a scheduled collect(). Returns
    every candidate found (no auto-pick of "the main one"); an empty list means
    genuinely nothing found, not a raised error."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    # guarded_async_client validates url AND pins the connection to the IP(s)
    # that validation resolved (DNS-rebinding TOCTOU closure — AUDIT B3
    # follow-up; see backend.security.url_guard's module docstring).
    # follow_redirects=False: a validated URL can still 30x-redirect to a
    # private/loopback/fleet address (SSRF via redirect) — this endpoint takes
    # a raw user-supplied homepage URL at setup time, so it's a prime target.
    # With redirects disabled, response.url == url, so the same pinned
    # client's connection (bound to url's hostname) is also correct for the
    # same-host fallback-probe loop below.
    try:
        client, url = await guarded_async_client(url, follow_redirects=False, timeout=10)
    except SSRFValidationError:
        return []

    async with client as opened_client:
        try:
            response = await opened_client.get(url)
            response.raise_for_status()
        except Exception:
            return []

        soup = BeautifulSoup(response.text, "lxml")
        for link in soup.find_all("link", rel="alternate"):
            mime = (link.get("type") or "").lower()
            href = link.get("href")
            if mime in _FEED_MIME_TYPES and href:
                feed_url = urljoin(str(response.url), href)
                # The derived feed_url is attacker-influenceable (comes from the
                # fetched page's own <link href>), so it needs the same check as
                # the original url, not just a "we already validated the site" pass.
                try:
                    feed_url = await avalidate_public_url(feed_url)
                except SSRFValidationError:
                    continue
                if feed_url not in seen:
                    seen.add(feed_url)
                    candidates.append({"url": feed_url, "title": link.get("title")})

        if candidates:
            return candidates

        # Nothing declared in <head> — probe common paths as a fallback. Same
        # host as the pinned client's connection (see guarded_async_client
        # call above), so reusing opened_client keeps the pin in effect here.
        parsed = urlparse(str(response.url))
        base = f"{parsed.scheme}://{parsed.netloc}"
        for path in _COMMON_FEED_PATHS:
            probe_url = base + path
            try:
                probe_url = await avalidate_public_url(probe_url)
            except SSRFValidationError:
                continue
            try:
                probe = await opened_client.get(probe_url)
            except Exception:
                continue
            content_type = (
                probe.headers.get("content-type", "").split(";")[0].strip().lower()
            )
            if (
                probe.status_code == 200
                and content_type in _FEED_MIME_TYPES
                and probe_url not in seen
            ):
                seen.add(probe_url)
                candidates.append({"url": probe_url, "title": None})

    return candidates


async def fetch_remote_opml(url: str) -> str:
    """Fetch a public OPML catalog without allowing redirects or unbounded
    response bodies.

    GitHub source catalogs are commonly published as raw ``.opml`` files.
    This setup-time helper keeps the same SSRF and DNS-rebinding protections
    as feed discovery, and caps downloads before the catalog reaches the XML
    parser.
    """
    try:
        client, validated_url = await guarded_async_client(
            url, follow_redirects=False, timeout=20
        )
    except SSRFValidationError as exc:
        raise ValueError(str(exc)) from exc

    try:
        chunks: list[bytes] = []
        size = 0
        async with client as opened_client:
            async with opened_client.stream("GET", validated_url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_REMOTE_OPML_BYTES:
                        raise ValueError("OPML catalog exceeds the 2 MB import limit")
                    chunks.append(chunk)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"unable to fetch OPML catalog: {exc}") from exc

    try:
        return b"".join(chunks).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("OPML catalog must be UTF-8 encoded") from exc


def parse_opml(xml_text: str) -> list[dict[str, str]]:
    """Walk an OPML document's (possibly nested, folder-grouped) <outline>
    elements and collect every one carrying xmlUrl (a feed). Malformed XML
    raises ValueError so the caller can 400 instead of 500."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"invalid OPML: {exc}") from exc

    entries: list[dict[str, str]] = []
    seen: set[str] = set()

    def _walk(node: ET.Element, folders: tuple[str, ...] = ()) -> None:
        for outline in node.findall("outline"):
            feed_url = outline.get("xmlUrl")
            label = outline.get("title") or outline.get("text")
            if feed_url and feed_url not in seen:
                seen.add(feed_url)
                entry = {"url": feed_url, "title": label or feed_url}
                if folders:
                    entry["group"] = folders[-1]
                entries.append(entry)

            child_folders = folders
            if not feed_url and label:
                child_folders = (*folders, label)
            _walk(outline, child_folders)

    body = root.find("body")
    _walk(body if body is not None else root)
    return entries


def _source_group_slug(value: str) -> str:
    normalized = re.sub(r"[^\w]+", "-", value.casefold(), flags=re.UNICODE)
    return normalized.strip("-_")[:80]


async def bulk_import_rss(
    session: AsyncSession,
    entries: list[dict[str, str]],
    *,
    catalog_url: str | None = None,
) -> tuple[list[DataSource], list[str]]:
    """Create one disabled channel_type="rss" DataSource per entry, deduped
    against BOTH already-stored sources and duplicates within the same OPML
    file. Lands disabled — a human reviews and enables, not an auto-live
    firehose from one file upload. Returns (created rows, skipped feed_urls)."""
    existing = (
        await session.execute(select(DataSource).where(DataSource.channel_type == "rss"))
    ).scalars().all()
    existing_urls = {s.channel_config.get("feed_url") for s in existing}

    created: list[DataSource] = []
    skipped: list[str] = []
    seen_this_batch: set[str] = set()

    for entry in entries:
        feed_url = entry["url"]
        if feed_url in existing_urls or feed_url in seen_this_batch:
            skipped.append(feed_url)
            continue
        seen_this_batch.add(feed_url)
        source_group = _source_group_slug(entry.get("group", ""))
        channel_config: dict[str, str] = {"feed_url": feed_url}
        tags = ["rss"]
        if source_group:
            channel_config["source_group"] = source_group
            tags.append(f"source-group:{source_group}")
        if catalog_url:
            channel_config["catalog_url"] = catalog_url
            tags.append("catalog:opml")
        source = DataSource(
            name=entry.get("title") or feed_url,
            channel_type="rss",
            channel_config=channel_config,
            enabled=False,
            tags=tags,
        )
        session.add(source)
        created.append(source)

    if created:
        await session.flush()
        for source in created:
            await session.refresh(source)

    return created, skipped
