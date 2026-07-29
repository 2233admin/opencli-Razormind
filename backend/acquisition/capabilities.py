"""Runtime-probed capability catalog for managed GEO acquisition."""

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

from backend.acquisition.registry import (
    OHMYOPENCLI_COMMIT,
    OPENCLI_VERSION,
    CapabilityRegistration,
    list_capability_registrations,
)
from backend.opencli_runtime import resolve_opencli_bin
from backend.schemas.acquisition import CapabilityDescriptor

COMMAND_TIMEOUT_SECONDS = 15.0


def _opencli_environment(*, cdp_endpoint: str | None = None) -> dict[str, str]:
    """Build one unambiguous OpenCLI browser-routing environment."""
    env = os.environ.copy()
    env.pop("OPENCLI_DAEMON_HOST", None)
    env.pop("OPENCLI_DAEMON_PORT", None)
    if cdp_endpoint is None:
        env.pop("OPENCLI_CDP_ENDPOINT", None)
    else:
        env["OPENCLI_CDP_ENDPOINT"] = cdp_endpoint
    return env


async def _command(*args: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=COMMAND_TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return 1, ""
        return process.returncode or 0, stdout.decode(errors="replace")
    except OSError:
        return 1, ""


async def _runtime_is_installed() -> bool:
    from backend.config import get_settings

    root = os.path.abspath(get_settings().ohmyopencli_root)
    commit_rc, commit = await _command("git", "-C", root, "rev-parse", "HEAD")
    if commit_rc != 0 or commit.strip() != OHMYOPENCLI_COMMIT:
        return False

    for source_commit in dict.fromkeys(
        registration.source_commit
        for registration in list_capability_registrations()
    ):
        source_rc, _ = await _command(
            "git",
            "-C",
            root,
            "merge-base",
            "--is-ancestor",
            source_commit,
            "HEAD",
        )
        if source_rc != 0:
            return False

    dirty_rc, dirty_output = await _command(
        "git",
        "-C",
        root,
        "status",
        "--porcelain",
        "--untracked-files=no",
    )
    if dirty_rc != 0 or dirty_output.strip():
        return False

    opencli_bin = resolve_opencli_bin()
    version_rc, version_output = await _command(
        opencli_bin,
        "--version",
        env=_opencli_environment(),
    )
    versions = re.findall(r"\d+\.\d+\.\d+", version_output)
    if version_rc != 0 or OPENCLI_VERSION not in versions:
        return False

    return True


async def _registration_is_available(
    registration: CapabilityRegistration,
) -> bool:
    opencli_bin = resolve_opencli_bin()
    command_rc, command_output = await _command(
        opencli_bin,
        *registration.probe_args,
        env=_opencli_environment(),
    )
    if command_rc != 0 or registration.help_marker not in command_output:
        return False

    patch_env = _opencli_environment(cdp_endpoint="http://127.0.0.1:9")
    patch_rc, patch_output = await _command(
        opencli_bin,
        *registration.route_probe_args,
        env=patch_env,
    )
    return patch_rc != 0 and registration.route_probe_error in patch_output


def _profile_unavailable_reason(profile_kind: str) -> str:
    return (
        "no_clean_profile"
        if profile_kind == "anonymous"
        else f"no_{profile_kind}_profile"
    )


def _profile_endpoints(profile_kind: str) -> tuple[object | None, list[str]]:
    from backend.browser_pool import get_pool

    try:
        pool = get_pool()
    except RuntimeError:
        return None, []
    return pool, [
        endpoint
        for endpoint in pool.endpoints
        if pool.get_profile_kind(endpoint) == profile_kind
    ]


def _browser_environment(pool: Any, endpoint: str) -> dict[str, str]:
    env = os.environ.copy()
    if pool.get_mode(endpoint) == "bridge":
        env.pop("OPENCLI_CDP_ENDPOINT", None)
        env["OPENCLI_DAEMON_HOST"] = urlparse(endpoint).hostname or "agent-1"
        env["OPENCLI_DAEMON_PORT"] = "19825"
    else:
        env.pop("OPENCLI_DAEMON_HOST", None)
        env.pop("OPENCLI_DAEMON_PORT", None)
        env["OPENCLI_CDP_ENDPOINT"] = endpoint
    return env


def _json_payload(output: str) -> dict | None:
    start = next((index for index, char in enumerate(output) if char in "[{"), None)
    if start is None:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(output[start:])
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else None
    return parsed if isinstance(parsed, dict) else None


async def _session_is_ready(
    registration: CapabilityRegistration,
    pool: Any,
    endpoint: str,
) -> bool:
    if not registration.session_probe_args:
        return True
    from backend.config import get_settings

    if get_settings().collection_mode == "agent":
        from backend.channels.opencli_channel import (
            _collect_via_agent,
            _collect_via_ws_agent,
        )

        site, command = registration.session_probe_args[:2]
        mode = pool.get_mode(endpoint)
        get_protocol = getattr(pool, "get_agent_protocol", None)
        get_agent_url = getattr(pool, "get_agent_url", None)
        protocol = get_protocol(endpoint) if get_protocol else "http"
        agent_url = (get_agent_url(endpoint) if get_agent_url else None) or endpoint
        if protocol == "ws":
            result = await _collect_via_ws_agent(
                agent_url, site, command, {}, [], "json", mode, None
            )
        else:
            result = await _collect_via_agent(
                agent_url, site, command, {}, [], "json", mode, None
            )
        payload = result.items[0] if result.success and result.items else None
        rc = 0 if payload is not None else 1
    else:
        opencli_bin = resolve_opencli_bin()
        rc, output = await _command(
            opencli_bin,
            *registration.session_probe_args,
            env=_browser_environment(pool, endpoint),
        )
        payload = _json_payload(output)
    return bool(
        rc == 0
        and payload
        and payload.get("unattendedReady") is True
        and payload.get("loginDetected") is False
        and payload.get("promptInputDetected") is True
        and (
            registration.session_expected_host is None
            or urlparse(str(payload.get("url", ""))).hostname
            == registration.session_expected_host
        )
    )


async def probe_capabilities() -> list[CapabilityDescriptor]:
    """Publish only the fixed runtime's real command registrations."""
    if not await _runtime_is_installed():
        return []

    descriptors = []
    for registration in list_capability_registrations():
        if not await _registration_is_available(registration):
            continue
        pool, endpoints = _profile_endpoints(registration.required_profile_kind)
        ready = bool(endpoints)
        unavailable_reason = (
            None
            if ready
            else _profile_unavailable_reason(registration.required_profile_kind)
        )
        if ready and registration.session_probe_args:
            ready = any(
                [
                    await _session_is_ready(registration, pool, endpoint)
                    for endpoint in endpoints
                ]
            )
            if not ready:
                unavailable_reason = (
                    registration.session_unavailable_reason
                    or "browser_session_not_ready"
                )
        descriptors.append(
            CapabilityDescriptor(
                capability_id=registration.capability_id,
                capability_version=registration.capability_version,
                output_schema_version=registration.output_schema_version,
                target=registration.target,
                ready=ready,
                runtime=registration.runtime_identity(),
                unavailable_reason=None if ready else unavailable_reason,
            )
        )
    return descriptors
