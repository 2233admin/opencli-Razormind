"""Outbound-fetch SSRF guard.

Deployment context (see AUDIT item B3): this system runs on a LAN reachable
over a NetBird/WireGuard mesh, not exposed via public port-mapping — so
*inbound* auth is handled by the WG layer and is out of scope here. SSRF is an
*outbound* threat instead: a channel/notifier/processor fetches a URL that
came from a user or from DB-stored config (or, worse, from content a remote
server chose to return, e.g. a redirect Location), and the server itself ends
up reaching an internal fleet peer (100.80.x.x), a localhost service (redis,
postgres), or exfiltrating an LLM provider API key to an attacker-controlled
public host. WireGuard does not protect outbound traffic, which is exactly
why this module exists.

``validate_public_url`` is the single choke point every outbound-fetch call
site in this codebase should call before opening a connection to a
user/DB-supplied URL. It:

  * requires ``http``/``https`` scheme (rejects ``file://``, ``gopher://``,
    ``ftp://``, etc — these can hit local files or talk to unintended
    protocols on internal-only ports),
  * resolves the hostname and rejects the URL if *any* resolved address is
    loopback, private (RFC1918 / ULA), link-local (including the cloud
    metadata address ``169.254.169.254``), unspecified, multicast, or
    otherwise not "globally reachable",
  * rejects a raw-IP host in those same ranges without needing DNS at all.

Callers that also need the resolved IP (to pin a redirect-safe connection,
see the DNS-rebinding note below) can use :func:`validate_public_url_and_ip`.

DNS-rebinding closure — ``guarded_async_client`` / ``PinnedAsyncHTTPTransport``
--------------------------------------------------------------------------
This module used to resolve and validate the hostname *once*, at call time,
without pinning the actual TCP connection to the IP that was validated — a
window in which a sufficiently motivated attacker who controls DNS for the
target hostname could rebind the name to a private IP *between* our check and
httpx's own connect() a moment later (TOCTOU).

That gap is now closed for plain-httpx call sites via
:func:`guarded_async_client`, which returns an ``httpx.AsyncClient`` whose
transport is a :class:`PinnedAsyncHTTPTransport`. The mechanism:

  * :class:`PinnedAsyncHTTPTransport` subclasses ``httpx.AsyncHTTPTransport``
    (so it inherits its TLS/proxy/limits setup unchanged) and swaps in its own
    ``httpcore.AsyncConnectionPool`` built with a ``network_backend`` that
    wraps :class:`_PinnedNetworkBackend`.
  * ``_PinnedNetworkBackend.connect_tcp(host, port, ...)`` is the *only* hook
    that changes: when ``host`` matches the guarded hostname, it dials one of
    the validated IPs instead of letting the OS resolver run again — this is
    exactly the seam httpcore exposes for a custom resolver
    (``httpcore.AsyncConnectionPool(network_backend=...)``).
  * Crucially, TLS SNI and certificate verification are **not** affected: per
    ``httpcore``'s own ``AsyncHTTPConnection._connect``, ``server_hostname``
    for ``start_tls`` defaults to the connection's ``Origin.host`` — i.e. the
    *original hostname from the URL* — completely independent of what
    ``network_backend.connect_tcp`` actually dialed. So the socket connects to
    the pinned IP while the ``Host`` header, SNI, and cert verification all
    still use the real hostname: HTTPS keeps working exactly as if no pinning
    were in place.
  * Defense in depth: ``_PinnedNetworkBackend.connect_tcp`` re-runs
    :func:`is_ip_blocked` on the IP it is about to dial (not just the IPs
    validated a moment earlier), so even a compromised/rebound resolver
    swapped in after validation can't smuggle a blocked address through.

:func:`guarded_async_client` is the one-stop call for callers that just want
"validate this URL and get me a client safe to fetch it with" — it validates
the URL, builds the pinned client, and returns ``(client, validated_url)``.
Callers that need a transport instead of a full client (rare) can use
:class:`PinnedAsyncHTTPTransport` directly.

Residual scope — SDK-only sites: a few call sites (LLM SDKs — ``openai``'s
``AsyncOpenAI``, ``crawl4ai``'s ``LLMConfig``) don't take an httpx transport
in a way this module can cleanly intercept in every case, or go through a
vendor SDK's own connection handling. Those sites keep call-time
``validate_public_url``/``avalidate_public_url`` validation only (the
pre-existing mitigation) and retain a residual DNS-rebinding TOCTOU window —
documented at each such call site rather than silently left unmentioned.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import typing
from urllib.parse import urlparse

import httpx

__all__ = [
    "SSRFValidationError",
    "validate_public_url",
    "validate_public_url_and_ip",
    "is_ip_blocked",
    "resolve_hostname",
    "PinnedAsyncHTTPTransport",
    "guarded_async_client",
]

_ALLOWED_SCHEMES = {"http", "https"}

# RFC 6598 "Shared Address Space" (100.64.0.0/10), reserved for carrier-grade
# NAT. Not flagged by ipaddress.IPv4Address.is_private on all Python versions
# we support, but it MUST be blocked here: this is exactly the range NetBird
# (this deployment's WireGuard mesh) hands out to fleet peers (e.g.
# 100.80.x.x) — the deployment's own threat model calls these out as an SSRF
# target the WG layer does NOT protect against outbound.
_CGNAT_SHARED_SPACE = ipaddress.ip_network("100.64.0.0/10")


class SSRFValidationError(ValueError):
    """Raised when a URL fails the outbound-fetch safety check.

    Always a clear, actionable message — callers should surface this (or a
    wrapped version of it) rather than silently swallowing it, since a reject
    here is a security decision, not an ordinary network hiccup.
    """


def is_ip_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool = False
) -> bool:
    """True if ``ip`` must not be reached by an outbound fetch.

    Blocks loopback (127.0.0.0/8, ::1), RFC1918 private ranges, link-local
    (169.254.0.0/16 — this includes the cloud-metadata address
    169.254.169.254 — and fe80::/10), IPv6 unique-local (fc00::/7),
    unspecified (0.0.0.0, ::), reserved, and multicast addresses. Anything not
    covered by one of those categories is treated as globally reachable and
    allowed.

    ``allow_private`` (default ``False`` — every existing call site keeps
    today's behaviour unchanged): when ``True``, loopback / RFC1918-private /
    link-local / IPv6-unique-local / the NetBird CGNAT shared space
    (100.64.0.0/10) are treated as reachable. ``unspecified``/``multicast``/
    ``reserved`` are still always blocked — no legitimate provider endpoint
    lives there, so there is no reason to ever allow them.

    This is a narrow, explicit opt-in (model-provider runtime PR-B, decision #6). It exists
    because ``url_guard`` had **no** existing localhost/private-IP exemption
    mechanism, yet self-hosted LLM providers (``ModelProvider.provider_type
    == "local"`` — ollama on loopback, model-hotel on the NetBird fleet mesh)
    legitimately live at exactly the addresses this guard otherwise blocks.
    ``backend.llm.openai_compat.OpenAICompatAdapter`` is the only caller that
    ever passes ``allow_private=True``, and only for ``provider_type ==
    "local"`` — ``openai``/``claude`` providers are always validated with
    ``allow_private=False`` (the full, unmodified guard).
    """
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return True
    if allow_private:
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_SHARED_SPACE:
        return True
    return ip.is_loopback or ip.is_private or ip.is_link_local


def resolve_hostname(hostname: str) -> list[str]:
    """Resolve ``hostname`` to its IPv4/IPv6 addresses (blocking; run this off
    the event loop from async code — see :func:`validate_public_url`).

    Raises :class:`SSRFValidationError` if resolution fails (unknown host,
    DNS failure, etc.) — an unresolvable host can't be safely fetched either.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFValidationError(f"could not resolve host {hostname!r}: {exc}") from exc
    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise SSRFValidationError(f"host {hostname!r} resolved to no addresses")
    return sorted(addrs)


def _check_host_and_ips(hostname: str, ips: list[str], *, allow_private: bool = False) -> None:
    for raw_ip in ips:
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if is_ip_blocked(ip, allow_private=allow_private):
            raise SSRFValidationError(
                f"URL host {hostname!r} resolves to a non-public address "
                f"({raw_ip}) — blocked to prevent SSRF against internal "
                "services (localhost, LAN/fleet peers, cloud metadata, etc.)"
            )


def validate_public_url(url: str, *, allow_private: bool = False) -> str:
    """Validate ``url`` is safe to fetch from the server; return it normalized.

    Synchronous — performs a blocking DNS lookup. Call
    :func:`validate_public_url_and_ip` (or wrap this in
    ``asyncio.to_thread``/``run_in_executor``) from async code so a slow/
    hanging resolver doesn't stall the event loop.

    Raises :class:`SSRFValidationError` on any rejection:
      * missing/invalid URL, or a scheme other than http/https,
      * missing hostname,
      * hostname/raw-IP resolves to a loopback/private/link-local/
        unique-local/unspecified/multicast address (unless ``allow_private``
        — see :func:`is_ip_blocked`).
    """
    return validate_public_url_and_ip(url, allow_private=allow_private)[0]


def validate_public_url_and_ip(
    url: str, *, allow_private: bool = False
) -> tuple[str, list[str]]:
    """Like :func:`validate_public_url`, but also returns the resolved IP(s)
    (sorted) so a caller can pin a redirect-safe connection to them — see the
    module docstring's DNS-rebinding note for why that pinning isn't wired
    through httpx in this pass.

    ``allow_private``: see :func:`is_ip_blocked` — default ``False`` keeps
    every existing caller's behaviour unchanged.
    """
    if not url or not isinstance(url, str):
        raise SSRFValidationError("URL is required")

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SSRFValidationError(
            f"URL scheme {parsed.scheme!r} is not allowed (only http/https) — "
            f"rejected: {url!r}"
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError(f"URL has no hostname: {url!r}")

    # Raw-IP host: no DNS involved, but still must not be a blocked address.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        _check_host_and_ips(hostname, [str(literal_ip)], allow_private=allow_private)
        return url, [str(literal_ip)]

    ips = resolve_hostname(hostname)
    _check_host_and_ips(hostname, ips, allow_private=allow_private)
    return url, ips


async def avalidate_public_url(url: str, *, allow_private: bool = False) -> str:
    """Async-friendly wrapper: runs the (blocking DNS) validation off-thread
    via ``asyncio.to_thread`` so it never stalls the event loop. Prefer this
    from ``async def`` call sites; :func:`validate_public_url` remains for
    sync call sites (e.g. module-level helpers called before an event loop
    exists)."""
    return await asyncio.to_thread(validate_public_url, url, allow_private=allow_private)


async def avalidate_public_url_and_ip(
    url: str, *, allow_private: bool = False
) -> tuple[str, list[str]]:
    """Async ``asyncio.to_thread`` wrapper around
    :func:`validate_public_url_and_ip`."""
    return await asyncio.to_thread(
        validate_public_url_and_ip, url, allow_private=allow_private
    )


# ── DNS-rebinding-safe connection pinning ────────────────────────────────────
#
# See the module docstring's "DNS-rebinding closure" section for the full
# mechanism explanation. Summary: httpcore's AsyncConnectionPool accepts a
# ``network_backend`` whose ``connect_tcp(host, port, ...)`` is the single hook
# that actually opens the socket; everything else (TLS SNI/server_hostname,
# cert verification, the Host header) is derived by httpcore/httpx from the
# request's original hostname and is untouched by what IP connect_tcp dials.


class _PinnedNetworkBackend:
    """``httpcore``-shaped async network backend that pins one hostname to a
    fixed set of already-validated IPs.

    Wraps ``httpcore``'s own :class:`~httpcore._backends.auto.AutoBackend` (so
    every other backend behaviour — unix sockets, sleep/backoff — is
    unchanged) and overrides only ``connect_tcp``: when the requested ``host``
    matches ``self._hostname``, it dials one of ``self._ips`` instead of
    letting the OS resolver run a second time. Any other host (e.g. a proxy)
    passes through unpinned.

    Defense in depth: re-checks :func:`is_ip_blocked` on the IP it is about to
    dial — not just the IPs validated a moment earlier by the caller — so a
    resolver swapped out from under us after validation still can't smuggle a
    blocked address through this seam.
    """

    def __init__(
        self, hostname: str, ips: list[str], *, allow_private: bool = False
    ) -> None:
        self._hostname = hostname
        self._ips = ips
        self._allow_private = allow_private
        self._next_ip_index = 0
        from httpcore._backends.auto import AutoBackend

        self._delegate = AutoBackend()

    def _pick_ip(self) -> str:
        # Round-robin over the validated IPs (mirrors "any A/AAAA record is
        # fine" DNS behaviour) rather than always hammering the first one.
        ip = self._ips[self._next_ip_index % len(self._ips)]
        self._next_ip_index += 1
        return ip

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Any = None,
    ) -> typing.Any:
        dial_host = host
        if host == self._hostname:
            dial_host = self._pick_ip()
            try:
                ip_obj = ipaddress.ip_address(dial_host)
            except ValueError:
                ip_obj = None
            if ip_obj is not None and is_ip_blocked(ip_obj, allow_private=self._allow_private):
                raise SSRFValidationError(
                    f"pinned connect target {dial_host!r} for host {host!r} is a "
                    "non-public address — refused at connect time (SSRF guard "
                    "defense in depth)"
                )
        return await self._delegate.connect_tcp(
            dial_host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: typing.Any = None
    ) -> typing.Any:  # pragma: no cover - not used by any call site in this codebase
        return await self._delegate.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """``httpx.AsyncHTTPTransport`` that pins ``hostname`` to ``ips``.

    Inherits ``httpx.AsyncHTTPTransport.__init__`` verbatim (so TLS
    ``verify``/``cert``/``trust_env``, HTTP/1.1 vs HTTP/2, proxy and
    connection-limit configuration all work exactly as they would for a plain
    ``httpx.AsyncClient``) and then replaces the ``httpcore.AsyncConnectionPool``
    it built with an equivalent one constructed with a
    :class:`_PinnedNetworkBackend`. ``handle_async_request`` itself is
    unchanged (inherited) — it just delegates to ``self._pool`` either way.
    """

    def __init__(
        self,
        hostname: str,
        ips: list[str],
        *,
        allow_private: bool = False,
        verify: "bool | str" = True,
        http1: bool = True,
        http2: bool = False,
        **kwargs: typing.Any,
    ) -> None:
        super().__init__(verify=verify, http1=http1, http2=http2, **kwargs)
        import httpcore

        # super().__init__() already built self._pool (a plain
        # httpcore.AsyncConnectionPool, since we never pass proxy= — this
        # class doesn't need to support proxying) with the ssl_context/limits/
        # http1/http2/retries/etc this call was configured with. Re-read those
        # back off the pool it just built rather than recomputing verify/cert
        # into an ssl.SSLContext ourselves (that logic is private to
        # httpx._config.create_ssl_context) — then rebuild an equivalent pool
        # with our pinning network_backend swapped in. This is the only piece
        # of AsyncHTTPTransport.__init__'s behaviour we override.
        built_pool = self._pool
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=built_pool._ssl_context,
            max_connections=built_pool._max_connections,
            max_keepalive_connections=built_pool._max_keepalive_connections,
            keepalive_expiry=built_pool._keepalive_expiry,
            http1=built_pool._http1,
            http2=built_pool._http2,
            retries=built_pool._retries,
            local_address=built_pool._local_address,
            uds=built_pool._uds,
            socket_options=built_pool._socket_options,
            network_backend=_PinnedNetworkBackend(hostname, ips, allow_private=allow_private),
        )


async def guarded_async_client(
    url: str, *, allow_private: bool = False, **client_kwargs: typing.Any
) -> tuple[httpx.AsyncClient, str]:
    """Validate ``url`` (SSRF guard) and return ``(client, validated_url)``
    where ``client`` is an ``httpx.AsyncClient`` whose transport is pinned to
    the IP(s) that were just validated for ``url``'s hostname — closing the
    DNS-rebinding TOCTOU window between this validation and the connection
    ``client`` will actually make (see the module docstring).

    A raw-IP URL (no hostname to rebind) still goes through
    :class:`PinnedAsyncHTTPTransport` for consistency and the connect-time
    ``is_ip_blocked`` defense-in-depth re-check, pinned to that single IP.

    ``allow_private`` (default ``False``): see :func:`is_ip_blocked` — passed
    through to both the initial validation and the pinned transport's
    connect-time re-check. Only ``backend.llm.openai_compat`` passes ``True``,
    and only for ``ModelProvider.provider_type == "local"``.

    ``client_kwargs`` are forwarded to ``httpx.AsyncClient`` verbatim (timeout,
    headers, follow_redirects, etc) except ``transport``, which this function
    owns — passing it raises ``TypeError`` (same as httpx would for a
    duplicate keyword) so a caller doesn't accidentally silently lose pinning.
    """
    if "transport" in client_kwargs:
        raise TypeError("guarded_async_client() sets 'transport' itself — do not pass one")

    validated_url, ips = await avalidate_public_url_and_ip(url, allow_private=allow_private)
    hostname = urlparse(validated_url).hostname or ""
    transport = PinnedAsyncHTTPTransport(hostname, ips, allow_private=allow_private)
    client = httpx.AsyncClient(transport=transport, **client_kwargs)
    return client, validated_url
