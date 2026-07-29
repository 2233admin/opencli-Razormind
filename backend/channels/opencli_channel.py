"""OpenCLI channel: invokes opencli CLI tool and parses its output."""

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from backend.channels.base import (
    AbstractChannel,
    Capabilities,
    ChannelResult,
    FetchContext,
    FetchResult,
)
from backend.channels.registry import register_channel
from backend.opencli_runtime import configured_opencli_bin, resolve_opencli_bin

logger = logging.getLogger(__name__)

_DAEMON_PORT = 19825
# Binary to invoke. Override with OPENCLI_BIN env var if needed.
_OPENCLI_BIN = configured_opencli_bin()

# Cache: (bin, site, command) → (cached_at, value). TTL'd (C17): without
# expiry these live for the process lifetime, so an opencli binary upgrade
# that adds/renames options keeps getting routed with the old flag set until
# the admin process restarts.
_CACHE_TTL_SECONDS = 3600  # 1 hour

_help_cache: dict[tuple[str, str, str], tuple[float, frozenset[str]]] = {}
_browser_requirement_cache: dict[tuple[str, str, str], tuple[float, bool]] = {}


def _cache_get(
    cache: dict[tuple[str, str, str], tuple[float, Any]],
    key: tuple[str, str, str],
) -> Any | None:
    """Return the cached value for key if present and not past its TTL, else None."""
    entry = cache.get(key)
    if entry is None:
        return None
    cached_at, value = entry
    if time.monotonic() - cached_at > _CACHE_TTL_SECONDS:
        del cache[key]
        return None
    return value


def _cache_set(
    cache: dict[tuple[str, str, str], tuple[float, Any]],
    key: tuple[str, str, str],
    value: Any,
) -> None:
    cache[key] = (time.monotonic(), value)


def _split_routing_parameters(
    parameters: dict[str, Any],
) -> tuple[tuple[str | None, str | None], dict[str, Any]]:
    """Separate Admin-only browser routing controls from capability arguments."""
    chrome_endpoint = parameters.get("chrome_endpoint") or None
    required_profile_kind = parameters.get("required_profile_kind") or None
    cli_parameters = {
        key: value
        for key, value in parameters.items()
        if key
        not in {
            "chrome_endpoint",
            "required_profile_kind",
            "execution_id",
            "_endpoint_preacquired",
        }
    }
    return (chrome_endpoint, required_profile_kind), cli_parameters


@asynccontextmanager
async def _browser_endpoint_lease(
    pool: Any,
    endpoint: str | None,
    required_profile_kind: str | None,
    *,
    preacquired: bool,
):
    """Reuse a runner-owned endpoint lease or acquire one for legacy callers."""
    if preacquired:
        if not endpoint:
            raise ValueError("A pre-acquired browser lease requires chrome_endpoint")
        yield endpoint
        return

    acquire_kwargs: dict[str, Any] = {"endpoint": endpoint}
    if required_profile_kind:
        acquire_kwargs["required_profile_kind"] = required_profile_kind
    async with pool.acquire(**acquire_kwargs) as leased_endpoint:
        yield leased_endpoint


async def _site_bound_agent_endpoint(pool: Any, site: str, session: AsyncSession) -> str | None:
    if not site:
        return None

    from backend.services import browser_service

    binding = await browser_service.get_binding_by_site(session, site)
    if not binding:
        return None

    endpoint = binding.browser_endpoint
    if endpoint not in pool.endpoints:
        logger.warning(
            "agent mode: site binding for %s points at endpoint %s outside the pool",
            site,
            endpoint,
        )
        return None
    if not pool.get_agent_protocol(endpoint):
        logger.warning(
            "agent mode: site binding for %s points at endpoint %s without an agent protocol",
            site,
            endpoint,
        )
        return None
    return endpoint


async def _select_agent_endpoint(pool: Any, site: str, session: AsyncSession) -> str | None:
    bound_endpoint = await _site_bound_agent_endpoint(pool, site, session)
    if bound_endpoint:
        logger.debug(
            "agent mode: selected site-bound endpoint %s for site=%s",
            bound_endpoint,
            site,
        )
        return bound_endpoint

    agent_eps = [ep for ep in pool.endpoints if pool.get_agent_protocol(ep)]
    if agent_eps:
        logger.debug(
            "agent mode: selected endpoint %s (has agent_protocol)",
            agent_eps[0],
        )
        return agent_eps[0]
    return None


async def _kill_subprocess(proc, *, platform: str | None = None) -> None:
    if proc is None or proc.returncode is not None:
        return
    try:
        if (platform or os.name) == "nt":
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(proc.pid, 9)  # SIGKILL on POSIX
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    await proc.wait()


def _process_group_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _peek_named_options(bin_path: str, site: str, command: str) -> frozenset[str] | None:
    """Cache-only lookup for the --option names accepted by a command.

    Returns None on a cache miss/expiry WITHOUT spawning a subprocess (C17).
    For callers that only want a nicer display string when the answer is
    already known for free — e.g. the collection-run event-log detail in
    pipeline.py, which must not pay a `--help` subprocess on the hot path
    just to format a log line. Real dispatch still calls
    ``_get_named_options`` (below), which fetches-and-caches for real.
    """
    return _cache_get(_help_cache, (bin_path, site, command))


async def _get_named_options(bin_path: str, site: str, command: str) -> frozenset[str]:
    """Return the set of --option names accepted by `opencli <site> <command>`.

    Runs `--help` once per (bin, site, command) triple and caches the result
    for _CACHE_TTL_SECONDS. Falls back to an empty set on any error so the
    caller can still try running.
    """
    import re
    key = (bin_path, site, command)
    cached = _cache_get(_help_cache, key)
    if cached is not None:
        return cached
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            bin_path, site, command, "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_process_group_kwargs(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        text = stdout.decode(errors="replace")
        # Extract every --flag name; strip built-ins that aren't user-facing options
        names = frozenset(re.findall(r"--([a-zA-Z][a-zA-Z0-9_-]*)", text)) - {
            "format", "verbose", "help"
        }
    except asyncio.CancelledError:
        await _kill_subprocess(proc)
        raise
    except Exception as exc:
        await _kill_subprocess(proc)
        logger.debug("could not fetch --help for %s %s: %s", site, command, exc)
        names = frozenset()
    _cache_set(_help_cache, key, names)
    return names


async def _command_requires_browser(bin_path: str, site: str, command: str) -> bool:
    """Return whether an opencli command declares it needs browser automation.

    OpenCLI site help is the adapter manifest. If it cannot be read, default to
    browser=True so authenticated/dynamic sites keep the safer routed behavior.
    """
    key = (bin_path, site, command)
    cached = _cache_get(_browser_requirement_cache, key)
    if cached is not None:
        return cached
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            bin_path,
            site,
            "--help",
            "-f",
            "yaml",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_process_group_kwargs(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        manifest = yaml.safe_load(stdout.decode(errors="replace")) or {}
        commands = manifest.get("commands", [])
        match = next(
            (
                item
                for item in commands
                if isinstance(item, dict) and item.get("name") == command
            ),
            None,
        )
        requires_browser = bool(match.get("browser", True)) if match else True
    except asyncio.CancelledError:
        await _kill_subprocess(proc)
        raise
    except Exception as exc:
        await _kill_subprocess(proc)
        logger.debug("could not inspect browser requirement for %s %s: %s", site, command, exc)
        requires_browser = True
    _cache_set(_browser_requirement_cache, key, requires_browser)
    return requires_browser


def _resolve_bin(mode: str) -> str:  # noqa: ARG001 — mode unused, kept for call-site compat
    return resolve_opencli_bin()


def _parse_json(raw: str) -> list[dict]:
    json_start = next((i for i, ch in enumerate(raw) if ch in ("{", "[")), None)
    if json_start is None:
        raise ValueError(f"No JSON found in output: {raw[:200]!r}")
    data = json.loads(raw[json_start:])
    return data if isinstance(data, list) else [data]


def _parse_yaml(raw: str) -> list[dict]:
    data = yaml.safe_load(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return [{"content": str(data)}]


def _parse_csv(raw: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(raw.strip()))
    return [row for row in reader]


def _parse_table(raw: str) -> list[dict]:
    """Parse cli-table3 Unicode box-drawing table into list of dicts."""
    lines = raw.splitlines()
    data_lines = [line for line in lines if line.strip().startswith("│")]
    if not data_lines:
        return [{"content": raw}]

    def split_row(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("│").split("│")]

    headers = split_row(data_lines[0])
    rows = []
    for line in data_lines[1:]:
        cells = split_row(line)
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
    return rows if rows else [{"content": raw}]


def _parse_markdown(raw: str) -> list[dict]:
    """Parse markdown table into list of dicts."""
    lines = [line.strip() for line in raw.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return [{"content": raw}]

    def split_row(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    headers = split_row(lines[0])
    rows = []
    for line in lines[2:]:
        cells = split_row(line)
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
    return rows if rows else [{"content": raw}]


_PARSERS = {
    "json":  _parse_json,
    "yaml":  _parse_yaml,
    "csv":   _parse_csv,
    "table": _parse_table,
    "md":    _parse_markdown,
}


async def _collect_via_agent(
    agent_url: str,
    site: str,
    command: str,
    args: dict,
    positional_args: list,
    output_format: str,
    mode: str,
    execution_id: str | None = None,
) -> ChannelResult:
    """Dispatch a collection request to a LAN agent server via HTTP POST.

    The cdp_endpoint is intentionally omitted: the agent server uses its own
    locally-configured Chrome (OPENCLI_CDP_ENDPOINT env var on the edge node).
    The pool endpoint is only a logical identifier used by the center for routing.
    """
    import httpx

    url = agent_url.rstrip("/") + "/collect"
    payload = {
        "site": site,
        "command": command,
        "args": args,
        "positional_args": positional_args,
        "format": output_format,
        "mode": mode,
        "execution_id": execution_id or "",
    }
    from backend.config import get_settings
    logger.info("agent dispatch | url=%s site=%s cmd=%s", url, site, command)
    try:
        settings = get_settings()
        headers = {"Authorization": f"Bearer {settings.api_auth_token}"}
        async with httpx.AsyncClient(timeout=settings.agent_http_timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except asyncio.CancelledError:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(f"{url}/{execution_id}/cancel", headers=headers)
        finally:
            raise
    except httpx.TimeoutException:
        logger.error("agent timeout | url=%s", url)
        return ChannelResult.fail(f"Agent request timed out: {url}")
    except Exception as exc:
        logger.error("agent error | url=%s error=%s", url, exc)
        return ChannelResult.fail(f"Agent request failed: {exc}")

    if not data.get("success"):
        err = data.get("error", "unknown agent error")
        logger.error("agent returned error | %s", err)
        return ChannelResult.fail(f"Agent error: {err}")

    items = data.get("items", [])
    agent_metadata = dict(data.get("metadata") or {})
    if data.get("runtime") is not None:
        agent_metadata["runtime"] = data["runtime"]
    if data.get("trace_artifact"):
        agent_metadata["trace_artifact"] = data["trace_artifact"]
    logger.info("agent done | site=%s cmd=%s items=%d", site, command, len(items))
    return ChannelResult.ok(
        items, site=site, command=command, node_url=agent_url, chrome_mode=mode,
        **agent_metadata,
    )


async def _collect_via_ws_agent(
    agent_url: str,
    site: str,
    command: str,
    args: dict,
    positional_args: list,
    output_format: str,
    mode: str,
    execution_id: str | None = None,
) -> ChannelResult:
    """Dispatch a collect request to a NAT agent via the persistent reverse WS channel."""
    from backend import ws_agent_manager

    logger.info("WS agent dispatch | agent=%s site=%s cmd=%s", agent_url, site, command)
    try:
        result = await ws_agent_manager.dispatch_collect(
            agent_url, site, command, args, positional_args, output_format, mode,
            request_id=execution_id,
        )
    except TimeoutError:
        logger.error("WS agent timeout | agent=%s", agent_url)
        return ChannelResult.fail(f"WS agent timed out: {agent_url!r}")
    except RuntimeError as exc:
        logger.error("WS agent not connected | agent=%s: %s", agent_url, exc)
        return ChannelResult.fail(f"WS agent not connected: {exc}")
    except Exception as exc:
        logger.error("WS agent error | agent=%s: %s", agent_url, exc)
        return ChannelResult.fail(f"WS agent error: {exc}")

    if not result.get("success"):
        err = result.get("error", "unknown agent error")
        logger.error("WS agent returned error | agent=%s: %s", agent_url, err)
        return ChannelResult.fail(f"WS agent error: {err}")

    items = result.get("items", [])
    agent_metadata = dict(result.get("metadata") or {})
    if result.get("runtime") is not None:
        agent_metadata["runtime"] = result["runtime"]
    if result.get("trace_artifact"):
        agent_metadata["trace_artifact"] = result["trace_artifact"]
    logger.info("WS agent done | site=%s cmd=%s items=%d", site, command, len(items))
    return ChannelResult.ok(
        items, site=site, command=command, node_url=agent_url, chrome_mode=mode,
        **agent_metadata,
    )


async def _check_bridge_ready(daemon_host: str, daemon_port: int) -> str | None:
    """Return an error string if the bridge extension is not ready, else None.

    The opencli daemon auto-starts on first use, so a missing daemon is not an
    error here — we only block when the daemon IS running but the extension is
    not yet connected (user needs to install the browser extension).
    """
    import httpx
    status_url = f"http://{daemon_host}:{daemon_port}/status"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(status_url, headers={"X-OpenCLI": "1"})
            data = resp.json()
    except Exception:
        # Daemon not running yet — opencli will start it automatically; not a blocker
        return None
    if not data.get("ok") or not data.get("extensionConnected"):
        return (
            "opencli Browser Bridge extension is not connected to the daemon. "
            "Install steps: "
            "1) Download the extension from GitHub Releases  "
            "2) Open chrome://extensions/ → Enable Developer Mode  "
            "3) Click 'Load unpacked' → select the extension folder  "
            "Or switch the data source to CDP mode if you have a Chrome CDP endpoint available."
        )
    return None


async def _snapshot_tab_ids(cdp_endpoint: str) -> set[str] | None:
    """Return the set of tab IDs currently open in Chrome.

    Returns ``None`` (not an empty set) when the snapshot itself fails (C20):
    an empty set means "genuinely no tabs are open right now", which is a
    valid baseline to diff against; ``None`` means "we don't know what
    existed before", and a caller that can't tell the two apart would treat
    every currently-open tab as newly-opened and close tabs the user opened
    themselves.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{cdp_endpoint}/json/list")
            return {t["id"] for t in resp.json() if "id" in t}
    except Exception as exc:
        logger.warning("cdp tab snapshot failed at %s: %s", cdp_endpoint, exc)
        return None


async def _cleanup_cdp_tabs(cdp_endpoint: str, pre_existing_ids: set[str]) -> None:
    """Close only tabs opened by opencli during collection.

    Compares current tab list against pre_existing_ids (snapshotted before the
    collect run) and closes only the new ones.  This prevents closing the user's
    personal Chrome tabs when connecting to a local (non-container) Chrome.

    After closing new tabs, ensures at least one blank page target remains so
    subsequent CDP connections always find an inspectable target.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{cdp_endpoint}/json/list")
            tabs = resp.json()
            remaining_pages = sum(1 for t in tabs if t.get("type") == "page")
            for tab in tabs:
                tab_id = tab.get("id", "")
                if tab.get("type") == "page" and tab_id not in pre_existing_ids:
                    try:
                        await client.get(f"{cdp_endpoint}/json/close/{tab_id}")
                        logger.info(
                            "cleanup: closed new tab %s url=%s",
                            tab_id,
                            tab.get("url", "")[:80],
                        )
                        remaining_pages -= 1
                    except Exception:
                        pass
            if remaining_pages == 0:
                try:
                    await client.put(f"{cdp_endpoint}/json/new")
                    logger.info("cleanup: opened blank tab to keep CDP target available")
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("cleanup: could not close CDP tabs at %s: %s", cdp_endpoint, exc)


async def _run_opencli(cmd: list[str], env: dict) -> tuple[int, str, str]:
    """Run opencli subprocess, return (returncode, stdout, stderr).

    Kills the process on timeout before re-raising so it doesn't linger.
    """
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **_process_group_kwargs(),
        )
        from backend.config import get_settings
        timeout = get_settings().opencli_timeout
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return int(proc.returncode or 0), stdout.decode(), stderr.decode().strip()
    except (TimeoutError, asyncio.CancelledError):
        await _kill_subprocess(proc)
        raise


def _extract_opencli_error(stderr_text: str) -> tuple[str | None, str | None]:
    """Read OpenCLI's structured error envelope without depending on its prose."""
    try:
        envelope = yaml.safe_load(stderr_text)
    except yaml.YAMLError:
        envelope = None
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        error = envelope["error"]
        code = str(error.get("code") or "").strip() or None
        message = str(error.get("message") or "").strip() or None
        return code, message

    # OpenCLI may append a human update notice after the YAML envelope. Retain
    # the typed code even when that makes the complete stderr invalid YAML.
    code_match = re.search(
        r"(?m)^\s+code:\s*['\"]?([A-Za-z0-9_-]+)['\"]?\s*$",
        stderr_text,
    )
    message_match = re.search(
        r"(?m)^\s+message:\s*(.+?)\s*$",
        stderr_text,
    )
    code = code_match.group(1) if code_match else None
    message = message_match.group(1).strip(" '\"") if message_match else None
    return code, message


def _artifact_sha256(artifact_ref: str) -> str | None:
    """Hash a trace file/directory so its persisted reference is auditable."""
    root = Path(artifact_ref)
    if not root.exists():
        return None
    files = [root] if root.is_file() else sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix(),
    )
    digest = hashlib.sha256()
    base = root.parent if root.is_file() else root
    for path in files:
        digest.update(path.relative_to(base).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


async def _collect_with_opencli_subprocess(
    cmd: list[str],
    env: dict,
    *,
    site: str,
    command: str,
    output_format: str,
    mode: str,
    chrome_mode: str | None = None,
) -> ChannelResult:
    try:
        returncode, stdout_text, stderr_text = await _run_opencli(cmd, env)
    except TimeoutError as exc:
        logger.error("opencli timeout | cmd=%s", " ".join(cmd))
        return ChannelResult.fail(
            "opencli command timed out after 120s", error_type=type(exc).__name__
        )
    except FileNotFoundError as exc:
        logger.error("opencli binary not found: %s", cmd[0])
        return ChannelResult.fail(
            f"opencli binary not found: {cmd[0]}", error_type=type(exc).__name__
        )
    except Exception as exc:
        logger.exception("opencli subprocess error | %s", exc)
        return ChannelResult.fail(
            f"Failed to run opencli: {exc}", error_type=type(exc).__name__
        )

    if stderr_text:
        logger.warning("opencli stderr | %s", stderr_text[:500])

    if returncode != 0:
        logger.error("opencli exit=%d | stderr=%s", returncode, stderr_text[:500])
        source_code, source_message = _extract_opencli_error(stderr_text)
        message = source_message or stderr_text or "OpenCLI command failed"
        return ChannelResult.fail(
            f"opencli exited with code {returncode}: {message}",
            error_type=source_code,
        )

    raw = stdout_text
    logger.debug("opencli stdout | %d chars | preview=%s", len(raw), raw[:200])
    parser = _PARSERS.get(output_format, _PARSERS["json"])
    try:
        items = parser(raw)
    except Exception as exc:
        logger.error(
            "opencli parse error | format=%s error=%s output_preview=%s",
            output_format,
            exc,
            raw[:300],
        )
        return ChannelResult.fail(
            f"Failed to parse opencli {output_format} output: {exc}",
            error_type=type(exc).__name__,
        )

    logger.info(
        "opencli done | site=%s cmd=%s mode=%s items=%d",
        site,
        command,
        mode,
        len(items),
    )
    metadata = {"site": site, "command": command}
    if chrome_mode:
        metadata["chrome_mode"] = chrome_mode
    trace_match = re.search(r"OpenCLI trace artifact:\s*([^\r\n]+)", stderr_text)
    if trace_match:
        trace_artifact = trace_match.group(1).strip()
        metadata["trace_artifact"] = trace_artifact
        trace_sha256 = await asyncio.to_thread(_artifact_sha256, trace_artifact)
        if trace_sha256:
            metadata["trace_sha256"] = trace_sha256
    return ChannelResult.ok(items, **metadata)


@register_channel
class OpenCLIChannel(AbstractChannel):
    """Collect data by running the opencli CLI tool.

    Migrated onto the thick ``fetch()`` contract (see ``fetch()`` below) via the
    same narrow-override pattern as ``BrowserActChannel`` (backend.channels.
    browser_act_channel): ``collect()`` stays the single source of truth for
    site/command routing, and ``fetch()`` only exists to flip channel_runner's
    ``channel_migrated`` check.
    """

    channel_type = "opencli"
    # Drives a real Chrome from the shared pool → must run on the node holding the
    # live session; the pipeline resolves a site-keyed browser binding for it.
    # incremental/paginated stay False: opencli's site/command catalog is an
    # external binary discovered at runtime via `--help` (see _get_named_options /
    # _command_requires_browser) — there is no cursor or page-token contract for
    # it anywhere in this codebase to drive a runner-owned pagination loop against.
    # default_rate is spelled out (rather than left to the dataclass default) to
    # document it's a deliberate choice, not an oversight: same 60/min every other
    # browser-driving channel (BrowserActChannel, SkillChannel) accepts, since
    # there's no empirical number specific to opencli to justify a different one.
    capabilities = Capabilities(session_affinity=True, default_rate="60/min")

    async def collect(
        self, config: dict[str, Any], parameters: dict[str, Any]
    ) -> ChannelResult:
        site = config.get("site", "")
        command = config.get("command", "")
        output_format = config.get("format", "json")

        execution_id = parameters.get("execution_id") or None
        endpoint_preacquired = parameters.get("_endpoint_preacquired") is True
        (chrome_endpoint, required_profile_kind), cli_params = (
            _split_routing_parameters(parameters)
        )
        raw_args: dict = {**config.get("args", {}), **cli_params}
        positional_args: list[str] = [str(v) for v in config.get("positional_args", [])]

        # Resolve which keys in raw_args are valid named --options for this command.
        # Any key not recognised by the binary is passed as a positional arg instead,
        # so configs written for older opencli versions continue to work after upgrades
        # where args like `query` became positional.
        opencli_bin_early = _resolve_bin("cdp")  # mode doesn't affect option names
        named_options = await _get_named_options(opencli_bin_early, site, command)
        args: dict = {}
        extra_positional: list[str] = []
        for k, v in raw_args.items():
            if named_options and k not in named_options:
                logger.debug(
                    "arg %r not a named option for %s/%s — passing as positional",
                    k,
                    site,
                    command,
                )
                extra_positional.append(str(v))
            else:
                args[k] = v
        # extra_positional goes first (before explicitly configured positional_args)
        positional_args = extra_positional + positional_args

        env = os.environ.copy()

        from backend.browser_pool import LocalBrowserPool, get_pool
        from backend.config import get_settings
        settings = get_settings()
        requires_browser = await _command_requires_browser(
            opencli_bin_early, site, command
        )

        if not requires_browser:
            cmd = [opencli_bin_early, site, command]
            cmd.extend(positional_args)
            for key, value in args.items():
                cmd.extend([f"--{key}", str(value)])
            cmd.extend(["-f", output_format])
            return await _collect_with_opencli_subprocess(
                cmd,
                env,
                site=site,
                command=command,
                output_format=output_format,
                mode="direct",
            )

        pool = get_pool()

        # In agent mode, prefer endpoints that have a registered agent_url/protocol.
        # The pool may also contain local chrome endpoints without agent metadata.
        _acquire_endpoint = chrome_endpoint
        if (
            settings.collection_mode == "agent"
            and not chrome_endpoint
            and isinstance(pool, LocalBrowserPool)
        ):
            # No request-scoped AsyncSession is available at this entry point (collect()'s
            # interface is config/parameters only), so — same as health_check() below —
            # we open one directly here and thread it explicitly into the helpers instead
            # of letting them each reach for their own AsyncSessionLocal() independently.
            from backend.database import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                _acquire_endpoint = await _select_agent_endpoint(pool, site, session)
            if not _acquire_endpoint:
                return ChannelResult.fail(
                    "No registered agent nodes available. Please add an agent node first."
                )

        async with _browser_endpoint_lease(
            pool,
            _acquire_endpoint,
            required_profile_kind,
            preacquired=endpoint_preacquired,
        ) as cdp_endpoint:
            mode = pool.get_mode(cdp_endpoint)
            # Agent mode: dispatch to remote edge node
            if settings.collection_mode == "agent":
                protocol = (
                    pool.get_agent_protocol(cdp_endpoint)
                    if isinstance(pool, LocalBrowserPool)
                    else "http"
                )
                get_agent_url = getattr(pool, "get_agent_url", None)
                agent_url = (
                    get_agent_url(cdp_endpoint) if get_agent_url else None
                ) or cdp_endpoint
                if not protocol:
                    return ChannelResult.fail(
                        f"Endpoint {cdp_endpoint} has no registered agent. "
                        "Set COLLECTION_MODE=local or add an agent node."
                    )
                if protocol == "http":
                    return await _collect_via_agent(
                        agent_url, site, command, args, positional_args, output_format, mode,
                        execution_id,
                    )
                elif protocol == "ws":
                    return await _collect_via_ws_agent(
                        agent_url, site, command, args, positional_args, output_format, mode,
                        execution_id,
                    )
                else:
                    logger.error(
                        "Unknown agent_protocol %r for endpoint %s",
                        protocol,
                        cdp_endpoint,
                    )
                    return ChannelResult.fail(f"Unknown agent_protocol: {protocol!r}")

            opencli_bin = _resolve_bin(mode)

            cmd = [opencli_bin, site, command]
            cmd.extend(positional_args)
            for key, value in args.items():
                cmd.extend([f"--{key}", str(value)])
            cmd.extend(["-f", output_format])

            if mode == "bridge":
                daemon_host = urlparse(cdp_endpoint).hostname or "agent-1"
                env.pop("OPENCLI_CDP_ENDPOINT", None)
                env["OPENCLI_DAEMON_HOST"] = daemon_host
                env["OPENCLI_DAEMON_PORT"] = str(_DAEMON_PORT)
                logger.info(
                    "opencli bridge | cmd=%s daemon=%s:%s",
                    " ".join(cmd),
                    daemon_host,
                    _DAEMON_PORT,
                )
                bridge_err = await _check_bridge_ready(daemon_host, _DAEMON_PORT)
                if bridge_err:
                    logger.warning(
                        "bridge readiness probe failed; trying opencli anyway: %s",
                        bridge_err,
                    )
            else:
                env["OPENCLI_CDP_ENDPOINT"] = cdp_endpoint
                logger.info("opencli cdp | cmd=%s cdp=%s", " ".join(cmd), cdp_endpoint)

            pre_tab_ids: set[str] | None = set()
            if mode == "cdp":
                pre_tab_ids = await _snapshot_tab_ids(cdp_endpoint)

            result = await _collect_with_opencli_subprocess(
                cmd,
                env,
                site=site,
                command=command,
                output_format=output_format,
                mode=mode,
                chrome_mode=mode,
            )

            if mode == "cdp":
                if pre_tab_ids is None:
                    # C20: no trustworthy baseline, so we can't tell newly-opened
                    # tabs from ones the user already had open — skip cleanup
                    # this run rather than risk closing the user's own tabs.
                    logger.warning(
                        "cdp cleanup skipped for %s: pre-collection tab snapshot "
                        "failed, can't distinguish new tabs from pre-existing ones",
                        cdp_endpoint,
                    )
                else:
                    await _cleanup_cdp_tabs(cdp_endpoint, pre_tab_ids)

            return result

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        """Thick-contract entry point, overridden narrowly (same pattern as
        ``BrowserActChannel.fetch()``, backend.channels.browser_act_channel:454) so
        that ``type(chan).fetch is not AbstractChannel.fetch`` and
        ``channel_runner.run_channel`` (backend.pipeline.channel_runner) treats
        opencli as migrated: it builds a ``RateLimitedClient`` from
        ``capabilities.default_rate`` for the run instead of skipping it, and any
        failure's ``error_type`` (already set by ``_collect_with_opencli_subprocess``
        for TimeoutError/FileNotFoundError/OSError/JSON-parse errors — see that
        function) reaches ``error_taxonomy.effective_error_type`` directly instead
        of relying on the inherited default doing the identical thing implicitly.

        ``collect()`` remains the single source of truth for site/command routing
        (direct subprocess vs. LAN-agent HTTP vs. WS-agent dispatch, browser-pool
        acquisition, CDP tab snapshot/cleanup) — this override delegates straight
        to the inherited default, because unlike ``BrowserActChannel`` there is no
        ``ctx.source_id``-driven credential lookup to thread through: opencli has
        no per-source encrypted-credential path (``capabilities.auth_kind`` stays
        "none", so ``run_channel`` resolves ``AuthContext`` without a DB hit).

        ``ctx.http`` is deliberately NOT threaded into ``collect()``: opencli's
        transport is a local subprocess (direct/cdp/bridge modes) or a LAN-agent
        HTTP/WS dispatch to an internal node (``_collect_via_agent`` /
        ``_collect_via_ws_agent``) authenticated with the fleet's own bearer
        token — neither is the public-API/SSRF-guarded shape ``ctx.http``'s
        rate-limited client is built for. Same accepted trade-off
        ``BrowserActChannel.fetch()`` documents: the ``RateLimitedClient`` the
        runner builds but this channel never reads is one Python object for the
        run's duration, not an open socket.
        """
        return await AbstractChannel.fetch(self, ctx)

    async def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("site"):
            errors.append("'site' is required for opencli channel")
        if not config.get("command"):
            errors.append("'command' is required for opencli channel")
        return errors

    async def health_check(
        self, config: dict[str, Any] | None = None, source_id: str | None = None
    ) -> bool:
        """Two-tier: cheap liveness (binary on PATH) always runs first and
        short-circuits on failure; deep readiness (a real browser reachable
        via CDP) only runs in local cdp/bridge mode — agent mode dispatches
        to a remote node with its own registration/health concern, and a
        bridge-mode endpoint has no local /json/version to hit. Acquires a
        pool slot only for the duration of the probe, not held afterward.
        When `config.site` has a browser binding (same lookup pipeline.py
        does before collect()), the probe targets that bound endpoint
        instead of an arbitrary pool member, so multi-endpoint pools give a
        result that reflects this source's actual endpoint."""
        resolved_bin = resolve_opencli_bin()
        if not os.path.isfile(resolved_bin) and resolved_bin == configured_opencli_bin():
            return False

        from backend.config import get_settings
        if get_settings().collection_mode == "agent":
            return True

        from backend.browser_pool import get_pool
        try:
            pool = get_pool()
        except RuntimeError:
            return True  # pool not initialized yet (e.g. tested standalone) — binary check stands

        acquire_endpoint: str | None = None
        site = (config or {}).get("site")
        if site:
            from backend.database import AsyncSessionLocal
            from backend.services import browser_service
            async with AsyncSessionLocal() as session:
                binding = await browser_service.get_binding_by_site(session, site)
                if binding:
                    acquire_endpoint = binding.browser_endpoint

        try:
            async with pool.acquire(endpoint=acquire_endpoint) as cdp_endpoint:
                if pool.get_mode(cdp_endpoint) != "cdp":
                    return True  # bridge mode: reachability is the daemon's concern, not ours
                import httpx
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{cdp_endpoint}/json/version")
                    resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("opencli health_check: CDP endpoint unreachable: %s", exc)
            return False
