import asyncio
import fnmatch
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from jsonschema import ValidationError, validate
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.browser import (
    BrowserCapabilityInvocation,
    BrowserInstance,
    BrowserRuntimeBundle,
)
from backend.schemas.browser import RuntimeBundleManifest
from backend.services.browser_service import (
    BrowserRuntimeError,
    get_runtime_bundle,
    get_runtime_deployment,
)


def _host_allowed(host: str, allowed_hosts: list[str]) -> bool:
    normalised = host.rstrip(".").lower()
    return any(fnmatch.fnmatchcase(normalised, allowed.lower()) for allowed in allowed_hosts)


def _capability_for(bundle: BrowserRuntimeBundle, capability_name: str) -> Any:
    manifest = RuntimeBundleManifest.model_validate(bundle.manifest)
    return next((item for item in manifest.capabilities if item.name == capability_name), None)


def validate_capability_args(capability: Any, args: dict) -> None:
    """Shared preflight used before Space task persistence and runtime dispatch."""
    try:
        validate(instance=args, schema=capability.args_schema)
    except ValidationError as exc:
        raise BrowserRuntimeError("invalid_capability_args", "invalid capability args") from exc
    if capability.allowed_hosts:
        candidate_url = args.get("url")
        host = urlparse(candidate_url).hostname if isinstance(candidate_url, str) else None
        if not host or not _host_allowed(host, capability.allowed_hosts):
            raise BrowserRuntimeError("host_not_allowed", "requested host is not allowed")


async def _dispatch_capability(
    instance: BrowserInstance,
    capability: Any,
    args: dict,
) -> dict:
    if not instance.agent_url:
        raise BrowserRuntimeError(
            "agent_route_unavailable",
            "the selected browser slot has no agent route for capability invocation",
        )
    endpoint_host = urlparse(instance.endpoint).hostname
    agent_host = urlparse(instance.agent_url).hostname
    if not endpoint_host or agent_host != endpoint_host:
        raise BrowserRuntimeError(
            "untrusted_agent_route",
            "agent route must use the registered browser endpoint host",
        )
    payload = {
        "runtime": capability.runtime,
        "workflow": capability.action,
        "instructions": capability.action,
        "input": args,
        "config": capability.config,
    }
    if instance.agent_protocol == "ws":
        from backend import ws_agent_manager

        return await ws_agent_manager.send_agent_task(
            instance.agent_url, payload, on_event=lambda _: None
        )
    if instance.agent_protocol != "http":
        raise BrowserRuntimeError(
            "agent_route_unavailable", "the selected browser slot has no supported agent protocol"
        )
    token = os.environ.get("AGENT_API_TOKEN") or os.environ.get("API_AUTH_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{instance.agent_url.rstrip('/')}/runtime/invoke",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict):
        raise BrowserRuntimeError("invalid_agent_response", "agent returned a non-object result")
    return result


async def invoke_capability(
    session: AsyncSession,
    instance: BrowserInstance,
    capability_name: str,
    args: dict,
    gate: str | None,
    *,
    gate_authorized: bool = False,
    audit_input_payload: dict | None = None,
    commit_before_dispatch: bool = False,
) -> BrowserCapabilityInvocation:
    deployment = await get_runtime_deployment(session, instance.id)
    bundle = (
        await get_runtime_bundle(session, instance.runtime_bundle_id)
        if instance.runtime_bundle_id
        else None
    )
    manifest = RuntimeBundleManifest.model_validate(bundle.manifest) if bundle else None
    component_versions = list(deployment.loaded_components) if deployment is not None else []
    capability = _capability_for(bundle, capability_name) if bundle else None
    invocation = BrowserCapabilityInvocation(
        browser_instance_id=instance.id,
        capability=capability_name,
        desired_bundle_name=bundle.name if bundle else None,
        desired_bundle_version=bundle.version if bundle else None,
        loaded_bundle_version=deployment.loaded_bundle_version if deployment else None,
        component_versions=component_versions,
        input_payload=audit_input_payload if audit_input_payload is not None else args,
        risk=capability.risk if capability else "high",
        gate=gate,
    )
    session.add(invocation)
    await session.flush()
    started = time.perf_counter()
    try:
        if deployment is None or deployment.state != "READY":
            raise BrowserRuntimeError(
                "slot_not_ready", "capability invocation requires a READY browser slot"
            )
        if capability is None or manifest is None:
            raise BrowserRuntimeError(
                "unknown_capability",
                f"capability {capability_name!r} is not exposed by the desired bundle",
            )
        validate_capability_args(capability, args)
        loaded_component_ids = {
            item.get("id")
            for item in component_versions
            if isinstance(item, dict) and item.get("healthy") is True
        }
        if capability.component_id not in loaded_component_ids:
            raise BrowserRuntimeError(
                "capability_component_unavailable",
                f"component {capability.component_id!r} is not loaded and healthy",
            )
        if capability.required_gate is not None and (
            capability.required_gate != gate or not gate_authorized
        ):
            raise BrowserRuntimeError(
                "gate_not_satisfied",
                f"capability {capability_name!r} requires an authorized "
                f"{capability.required_gate!r} gate",
            )
        if commit_before_dispatch:
            # Space executors use expire_on_commit=False and own this session.
            # Persist the audit start, then release all locks before agent I/O.
            await session.commit()
        result = await _dispatch_capability(instance, capability, args)
        if commit_before_dispatch and result.get("type") == "error":
            raise BrowserRuntimeError("agent_invocation_failed", "agent reported a runtime failure")
        invocation.output_payload = result
        invocation.page_before = result.get("page_before")
        invocation.page_after = result.get("page_after")
    except asyncio.CancelledError:
        invocation.error = {"code": "capability_cancelled", "message": "capability call cancelled"}
        raise
    except BrowserRuntimeError as exc:
        invocation.error = {"code": exc.code, "message": str(exc)}
        raise
    except httpx.HTTPError as exc:
        invocation.error = {"code": "agent_invocation_failed", "message": str(exc)}
        raise BrowserRuntimeError("agent_invocation_failed", str(exc)) from exc
    except Exception as exc:
        invocation.error = {"code": "capability_invocation_failed", "message": str(exc)}
        raise BrowserRuntimeError("capability_invocation_failed", str(exc)) from exc
    finally:
        invocation.duration_ms = int((time.perf_counter() - started) * 1000)
        await session.flush()
    return invocation
