"""Runtime-probed capability catalog for managed GEO acquisition."""

import asyncio
import os
import re

from backend.acquisition.registry import (
    OHMYOPENCLI_COMMIT,
    OPENCLI_VERSION,
    CapabilityRegistration,
    list_capability_registrations,
)
from backend.opencli_runtime import resolve_opencli_bin
from backend.schemas.acquisition import CapabilityDescriptor

COMMAND_TIMEOUT_SECONDS = 15.0


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
    version_rc, version_output = await _command(opencli_bin, "--version")
    versions = re.findall(r"\d+\.\d+\.\d+", version_output)
    if version_rc != 0 or OPENCLI_VERSION not in versions:
        return False

    return True


async def _registration_is_available(
    registration: CapabilityRegistration,
) -> bool:
    opencli_bin = resolve_opencli_bin()
    command_rc, command_output = await _command(
        opencli_bin, *registration.probe_args
    )
    if command_rc != 0 or registration.help_marker not in command_output:
        return False

    patch_env = os.environ.copy()
    patch_env["OPENCLI_CDP_ENDPOINT"] = "http://127.0.0.1:9"
    patch_rc, patch_output = await _command(
        opencli_bin,
        *registration.route_probe_args,
        env=patch_env,
    )
    return patch_rc != 0 and registration.route_probe_error in patch_output


def _profile_available(profile_kind: str) -> bool:
    from backend.browser_pool import get_pool

    try:
        pool = get_pool()
    except RuntimeError:
        return False
    return any(
        pool.get_profile_kind(endpoint) == profile_kind for endpoint in pool.endpoints
    )


async def probe_capabilities() -> list[CapabilityDescriptor]:
    """Publish only the fixed runtime's real command registrations."""
    if not await _runtime_is_installed():
        return []

    descriptors = []
    for registration in list_capability_registrations():
        if not await _registration_is_available(registration):
            continue
        ready = _profile_available(registration.required_profile_kind)
        descriptors.append(
            CapabilityDescriptor(
                capability_id=registration.capability_id,
                capability_version=registration.capability_version,
                output_schema_version=registration.output_schema_version,
                ready=ready,
                runtime=registration.runtime_identity(),
                unavailable_reason=(
                    None
                    if ready
                    else (
                        "no_clean_profile"
                        if registration.required_profile_kind == "anonymous"
                        else f"no_{registration.required_profile_kind}_profile"
                    )
                ),
            )
        )
    return descriptors
