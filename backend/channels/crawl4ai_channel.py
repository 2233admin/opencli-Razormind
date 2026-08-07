"""Crawl4AI channel: JS-rendered pages + built-in anti-detection.

Two extraction paths: CSS-structured (JsonCssExtractionStrategy) when
'selectors' is configured — the default, no AI cost — falling back to
LLMExtractionStrategy when it isn't (targeted sources where writing a CSS
selector up front isn't practical: give an 'instruction' describing what to
pull instead). This is extraction — deciding what the *items* are — not the
pipeline's separate downstream AI *enrichment* step (backend.pipeline.pipeline
's collect -> normalize/store -> AI -> notify, which adds derived fields to
records that already exist); the two don't overlap.

LLM credentials reuse the same ModelProvider row the enrichment step and
AIAgent use (backend.models.provider) — configure a provider once, both paths
just work. No 'provider_id' in config → first enabled provider, same
autonomous-default convention as backend.pipeline.runner.

Deliberately does NOT go through backend.browser_pool / connect_over_cdp like
every other browser-touching channel (skill/opencli channels attach to an
already-running, human-logged-in browser via session_affinity). Crawl4AI's
whole reason for existing here is its own anti-detection browser management
(enable_stealth, magic mode); forcing it onto an externally-attached browser
would throw that away and leave nothing but a slower Playwright.
"""

import json
import logging
from typing import Any
from urllib.parse import urlparse

from backend.channels.base import (
    AbstractChannel,
    ChannelFetchError,
    ChannelResult,
    FetchContext,
    FetchResult,
)
from backend.channels.registry import register_channel
from backend.llm.factory import litellm_prefix_for
from backend.security.url_guard import SSRFValidationError, avalidate_public_url

logger = logging.getLogger(__name__)


@register_channel
class Crawl4AIChannel(AbstractChannel):
    """Collect data from JS-rendered pages via Crawl4AI's own managed browser."""

    channel_type = "crawl4ai"

    async def collect(
        self, config: dict[str, Any], parameters: dict[str, Any]
    ) -> ChannelResult:
        """Thin wrapper around fetch() — see api_channel.collect() for the
        pattern this mirrors."""
        ctx = FetchContext(config=config, params=parameters)
        try:
            result = await self.fetch(ctx)
        except ChannelFetchError as exc:
            cause = exc.__cause__
            return ChannelResult.fail(str(exc), error_type=type(cause).__name__ if cause else None)
        return ChannelResult.ok(result.items, **result.metadata)

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        config = ctx.config
        url: str = config.get("url", "")
        if not url:
            raise ChannelFetchError("crawl4ai channel: 'url' is required")
        try:
            url = await avalidate_public_url(url)
        except SSRFValidationError as exc:
            raise ChannelFetchError(
                f"crawl4ai URL rejected: {exc}", error_type="SSRFValidationError"
            ) from exc
        list_selector: str = config.get("list_selector", "")
        selectors: dict[str, str] = config.get("selectors", {})
        wait_for: str | None = config.get("wait_for")
        auth_config: dict = config.get("auth", {})

        cookies: list[dict] = []
        if auth_config.get("type") == "cookie":
            try:
                cookies = await self._resolve_cookies(url)
            except ChannelFetchError:
                raise
            except Exception as exc:
                raise ChannelFetchError(f"crawl4ai: cookie resolution failed: {exc}") from exc

        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
            from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
        except ImportError as exc:
            raise ChannelFetchError("crawl4ai package not installed") from exc

        if selectors:
            schema = {
                "baseSelector": list_selector or "body",
                "fields": [
                    {"name": name, "selector": sel, "type": "text"}
                    for name, sel in selectors.items()
                ],
            }
            extraction_strategy = JsonCssExtractionStrategy(schema)
        else:
            # _build_llm_strategy hits the DB (provider lookup) and can raise
            # unclassified errors that would otherwise escape collect()'s
            # ChannelFetchError-only catch, bypassing the retry/error-taxonomy
            # contract every other failure path here goes through.
            try:
                extraction_strategy = await self._build_llm_strategy(config)
            except ChannelFetchError:
                raise
            except Exception as exc:
                raise ChannelFetchError(f"crawl4ai: LLM strategy setup failed: {exc}") from exc

        browser_config = BrowserConfig(headless=True, enable_stealth=True, cookies=cookies or None)
        run_config = CrawlerRunConfig(
            extraction_strategy=extraction_strategy,
            cache_mode=CacheMode.BYPASS,
            magic=True,
            wait_for=wait_for,
        )

        try:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url=url, config=run_config)
        except ChannelFetchError:
            raise
        except Exception as exc:
            raise ChannelFetchError(f"crawl4ai request to {url} failed: {exc}") from exc

        if not result.success:
            err = result.error_message or "unknown error"
            raise ChannelFetchError(f"crawl4ai fetch failed: {err}")

        items: list[dict[str, Any]] = []
        if result.extracted_content:
            try:
                parsed = json.loads(result.extracted_content)
            except (json.JSONDecodeError, TypeError) as exc:
                # WIRING_GAP_LEDGER W1: error_type must be set so the
                # SCHEMA_DRIFT chain (error_kinds -> control.recorder) fires
                # instead of being dropped by recorder's `elif error_type is
                # not None` guard.
                raise ChannelFetchError(
                    "crawl4ai: could not parse extracted_content as JSON",
                    error_type=type(exc).__name__,
                ) from exc
            items = parsed if isinstance(parsed, list) else [parsed]

        return FetchResult(items=items, metadata={"url": url, "status_code": result.status_code})

    @staticmethod
    async def _build_llm_strategy(config: dict[str, Any]) -> Any:
        """No 'selectors' configured — fall back to instruction-driven LLM
        extraction. 'instruction'/provider availability are runtime state
        (not config shape), so they're checked here rather than in
        validate_config."""
        instruction: str = config.get("instruction", "")
        if not instruction:
            raise ChannelFetchError(
                "crawl4ai channel: no 'selectors' configured — provide 'instruction' "
                "for LLM-based extraction"
            )

        from crawl4ai.extraction_strategy import LLMExtractionStrategy

        llm_config = await Crawl4AIChannel._resolve_llm_config(config.get("provider_id"))
        extraction_schema = config.get("extraction_schema")

        return LLMExtractionStrategy(
            llm_config=llm_config,
            instruction=instruction,
            schema=extraction_schema,
            extraction_type="schema" if extraction_schema else "block",
            apply_chunking=config.get("apply_chunking", True),
        )

    @staticmethod
    async def _resolve_llm_config(provider_id: str | None) -> Any:
        """Same autonomous-default convention as backend.pipeline.runner: an
        explicit provider_id wins, otherwise the first enabled ModelProvider.

        model-provider runtime PR-E exception (decision #8): crawl4ai's LLM calls go through
        its own ``litellm``-backed ``LLMExtractionStrategy`` /
        ``AsyncWebCrawler`` — an internal client this module has no clean
        seam to route through ``backend.llm``'s adapters/resolver, so that
        call stays exactly as it was. What DOES move here is the
        provider-selection *resolution*: which litellm provider-name prefix
        (``"openai"`` / ``"anthropic"``) a given ``provider.provider_type``
        maps to now comes from :func:`backend.llm.factory.litellm_prefix_for`
        — the same place :func:`backend.llm.factory.get_adapter` dispatches
        adapters from — instead of an independently hand-maintained dict
        here that could silently drift out of sync with it. The provider
        *selection* itself (explicit ``provider_id`` vs. first-enabled) is
        untouched: it is not role/``model_defaults``-based like PR-D's
        resolver, so wiring ``ProviderResolver`` in here would change WHICH
        provider gets picked, not just how the prefix is computed — out of
        scope for a client-construction consolidation.
        """
        from crawl4ai import LLMConfig
        from sqlalchemy import select

        from backend.database import AsyncSessionLocal
        from backend.models.provider import ModelProvider

        async with AsyncSessionLocal() as session:
            if provider_id:
                provider = await session.get(ModelProvider, provider_id)
            else:
                result = await session.execute(
                    select(ModelProvider)
                    .where(ModelProvider.enabled.is_(True))
                    .order_by(ModelProvider.created_at.asc())
                )
                provider = result.scalars().first()

        if not provider or not provider.enabled:
            raise ChannelFetchError(
                "crawl4ai LLM extraction: no enabled model provider configured "
                "(add one under Providers first)"
            )

        # Key-exfil guard: a provider's base_url is DB-stored config, not a
        # hardcoded trusted endpoint — if it doesn't pass the SSRF/public-host
        # check, we must not attach the API key or call it (that would ship the
        # key to whatever internal/attacker host base_url points at). No
        # base_url configured (None → provider's own default endpoint) is fine
        # and is not validated here.
        #
        # Residual DNS-rebinding TOCTOU (AUDIT B3 follow-up, documented not
        # silently left): unlike this repo's own httpx call sites (see
        # backend.security.url_guard.guarded_async_client), LLMConfig hands
        # base_url to litellm/crawl4ai's own HTTP client internals, which this
        # module has no clean seam to pin to a validated IP through. This
        # call-time validate_public_url check remains the only mitigation
        # here — a DNS rebind between this check and litellm's own connect()
        # is not closed. Low practical severity (base_url is operator/DB
        # config, not attacker-supplied per-request input), but real.
        base_url = provider.base_url or None
        if base_url:
            try:
                base_url = await avalidate_public_url(base_url)
            except SSRFValidationError as exc:
                raise ChannelFetchError(
                    f"crawl4ai LLM extraction: provider base_url rejected: {exc}",
                    error_type="SSRFValidationError",
                ) from exc

        litellm_prefix = litellm_prefix_for(provider.provider_type)
        default_model = (
            "claude-haiku-4-5-20251001" if litellm_prefix == "anthropic" else "gpt-4o-mini"
        )
        return LLMConfig(
            provider=f"{litellm_prefix}/{provider.default_model or default_model}",
            api_token=provider.api_key or None,
            base_url=base_url,
        )

    @staticmethod
    async def _resolve_cookies(url: str) -> list[dict]:
        """auth.type == "cookie": borrow a real login session synced from
        CookieCloud. AuthManager.resolve_cookies() already returns
        Playwright-shaped dicts, which is exactly what BrowserConfig.cookies
        expects (Crawl4AI's browser layer is Playwright)."""
        from backend.auth.manager import AuthManager

        domain = urlparse(url).hostname or ""
        if not domain:
            return []
        return await AuthManager().resolve_cookies(domain)

    async def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("url"):
            errors.append("'url' is required for crawl4ai channel")
        if not config.get("selectors") and not config.get("instruction"):
            errors.append(
                "'selectors' (CSS extraction) or 'instruction' (LLM extraction) "
                "is required for crawl4ai channel"
            )
        return errors

    async def health_check(
        self, config: dict[str, Any] | None = None, source_id: str | None = None
    ) -> bool:
        """Two-tier: the crawl4ai package (this channel's "driver") must be
        importable at all, then, when a source's config is available, a real
        lightweight fetch of the target URL. Short timeout: liveness, not a
        full crawl."""
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
        except Exception as exc:
            logger.warning("crawl4ai health_check: package unavailable: %s", exc)
            return False

        if config is None:
            return True  # no source context to probe (e.g. called standalone)
        url: str = config.get("url", "")
        if not url:
            return False

        try:
            url = await avalidate_public_url(url)
        except SSRFValidationError as exc:
            logger.warning("crawl4ai health_check: URL rejected: %s", exc)
            return False

        cookies: list[dict] = []
        if config.get("auth", {}).get("type") == "cookie":
            cookies = await self._resolve_cookies(url)

        try:
            # Same anti-detection setup as fetch() — a probe without it can
            # false-fail against a source that's only reachable with stealth/magic.
            browser_config = BrowserConfig(
                headless=True, enable_stealth=True, cookies=cookies or None
            )
            async with AsyncWebCrawler(config=browser_config) as crawler:
                probe_config = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS, magic=True, page_timeout=5000
                )
                result = await crawler.arun(url=url, config=probe_config)
            return bool(result.success)
        except Exception as exc:
            logger.warning("crawl4ai health_check: %s unreachable: %s", url, exc)
            return False
