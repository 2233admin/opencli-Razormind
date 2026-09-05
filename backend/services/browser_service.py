from pathlib import PurePosixPath

from jsonschema import Draft202012Validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.browser import (
    BrowserBinding,
    BrowserInstance,
    BrowserRuntimeBundle,
    BrowserRuntimeDeployment,
)
from backend.schemas.browser import (
    BrowserInstanceConfigUpdate,
    BrowserInstanceCreate,
    BrowserRuntimeBundleCreate,
    RuntimeBundleManifest,
    SlotRuntimeReport,
)


class BrowserRuntimeError(ValueError):
    """A fail-closed runtime-bundle contract violation suitable for an HTTP 4xx."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _safe_component_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return (
        not candidate.is_absolute()
        and ".." not in candidate.parts
        and candidate.parts not in ((), (".",))
    )


def validate_runtime_manifest(manifest: RuntimeBundleManifest) -> dict:
    """Validate a manifest before persisting its immutable desired configuration."""

    component_ids: set[str] = set()
    for component in manifest.components:
        if component.id in component_ids:
            raise BrowserRuntimeError(
                "duplicate_component", f"component {component.id!r} is declared more than once"
            )
        if not _safe_component_path(component.path):
            raise BrowserRuntimeError(
                "unsafe_component_path",
                f"component {component.id!r} has a path outside its bundle directory",
            )
        component_ids.add(component.id)

    capability_names: set[str] = set()
    for capability in manifest.capabilities:
        if capability.name in capability_names:
            raise BrowserRuntimeError(
                "duplicate_capability",
                f"capability {capability.name!r} is declared more than once",
            )
        if capability.component_id not in component_ids:
            raise BrowserRuntimeError(
                "unknown_component",
                f"capability {capability.name!r} references an undeclared component",
            )
        if capability.risk != "low" and not capability.required_gate:
            raise BrowserRuntimeError(
                "missing_gate",
                f"{capability.risk}-risk capability {capability.name!r} requires a gate",
            )
        try:
            Draft202012Validator.check_schema(capability.args_schema)
        except Exception as exc:
            raise BrowserRuntimeError(
                "invalid_args_schema",
                f"capability {capability.name!r} has an invalid args_schema: {exc}",
            ) from exc
        capability_names.add(capability.name)

    return manifest.model_dump(mode="json")


def reconcile_runtime_state(
    bundle: BrowserRuntimeBundle | None,
    report: SlotRuntimeReport,
) -> tuple[str, list[str]]:
    """Pure desired-versus-loaded health reduction; only this may produce READY."""

    if bundle is None:
        return "DEGRADED", ["slot has no desired runtime bundle"]

    manifest = RuntimeBundleManifest.model_validate(bundle.manifest)
    diagnostics: list[str] = []
    if report.restart_required:
        diagnostics.append("slot reports restart required")
        return "RESTART_REQUIRED", diagnostics
    if report.loaded_bundle_name != bundle.name or report.loaded_bundle_version != bundle.version:
        diagnostics.append(
            f"desired bundle is {bundle.name}@{bundle.version}; "
            f"loaded is {report.loaded_bundle_name}@{report.loaded_bundle_version}"
        )
        return "CONFIG_DRIFT", diagnostics

    expected = {component.id: component for component in manifest.components}
    actual = {component.id: component for component in report.loaded_components}
    unknown = sorted(set(actual) - set(expected))
    if unknown:
        return "CONFIG_DRIFT", [f"loaded unknown components: {', '.join(unknown)}"]

    for component_id, component in expected.items():
        loaded = actual.get(component_id)
        if loaded is None:
            if component.required:
                state = (
                    "EXTENSION_FAILED"
                    if component.kind == "extension"
                    else "SCRIPT_FAILED"
                    if component.kind == "script"
                    else "DEGRADED"
                )
                return state, [f"required {component.kind} {component_id!r} is not loaded"]
            continue
        if loaded.kind != component.kind or loaded.version != component.version:
            return "CONFIG_DRIFT", [
                f"component {component_id!r} expected {component.kind}@{component.version}; "
                f"loaded {loaded.kind}@{loaded.version}"
            ]
        if not loaded.healthy:
            state = (
                "EXTENSION_FAILED"
                if component.kind == "extension"
                else "SCRIPT_FAILED"
                if component.kind == "script"
                else "DEGRADED"
            )
            return state, [
                loaded.diagnostic or f"{component.kind} {component_id!r} failed its self-check"
            ]

    loaded_component_ids = set(actual)
    expected_capabilities = {
        item.name for item in manifest.capabilities if item.component_id in loaded_component_ids
    }
    loaded_capabilities = set(report.capabilities)
    if loaded_capabilities != expected_capabilities:
        return "CONFIG_DRIFT", [
            "capability manifest drift: expected "
            f"{sorted(expected_capabilities)}, loaded {sorted(loaded_capabilities)}"
        ]
    if any(component.id == "violentmonkey" for component in manifest.components):
        userscripts_check = report.self_check.get("violentmonkey_user_scripts_access")
        if not isinstance(userscripts_check, dict) or userscripts_check.get("ok") is not True:
            return "EXTENSION_FAILED", [
                "Violentmonkey userScriptsAccess self-check did not report ok=true"
            ]
    if report.self_check.get("ok") is not True:
        return "DEGRADED", ["slot self-check did not report ok=true"]
    return "READY", []


def _profile_name(instance: BrowserInstance) -> str:
    return instance.profile_name or instance.endpoint


async def _ensure_unique_profile(
    session: AsyncSession,
    profile_name: str,
    *,
    excluding_instance_id: str | None = None,
) -> None:
    query = select(BrowserInstance).where(BrowserInstance.profile_name == profile_name)
    if excluding_instance_id is not None:
        query = query.where(BrowserInstance.id != excluding_instance_id)
    existing = (await session.execute(query)).scalar_one_or_none()
    if existing is not None:
        raise BrowserRuntimeError(
            "profile_in_use",
            f"profile {profile_name!r} is already assigned to another browser slot",
        )


async def list_bindings(session: AsyncSession) -> list[BrowserBinding]:
    result = await session.execute(select(BrowserBinding).order_by(BrowserBinding.site))
    return list(result.scalars().all())


async def get_binding(session: AsyncSession, binding_id: str) -> BrowserBinding | None:
    return await session.get(BrowserBinding, binding_id)


async def get_binding_by_site(session: AsyncSession, site: str) -> BrowserBinding | None:
    result = await session.execute(select(BrowserBinding).where(BrowserBinding.site == site))
    return result.scalar_one_or_none()


async def create_binding(
    session: AsyncSession, browser_endpoint: str, site: str, notes: str | None = None
) -> BrowserBinding:
    binding = BrowserBinding(browser_endpoint=browser_endpoint, site=site, notes=notes)
    session.add(binding)
    await session.flush()
    await session.refresh(binding)
    return binding


async def delete_binding(session: AsyncSession, binding_id: str) -> bool:
    result = await session.execute(delete(BrowserBinding).where(BrowserBinding.id == binding_id))
    return result.rowcount > 0


async def list_runtime_bundles(session: AsyncSession) -> list[BrowserRuntimeBundle]:
    result = await session.execute(
        select(BrowserRuntimeBundle).order_by(
            BrowserRuntimeBundle.name, BrowserRuntimeBundle.version
        )
    )
    return list(result.scalars().all())


async def get_runtime_bundle(session: AsyncSession, bundle_id: str) -> BrowserRuntimeBundle | None:
    return await session.get(BrowserRuntimeBundle, bundle_id)


async def create_runtime_bundle(
    session: AsyncSession, payload: BrowserRuntimeBundleCreate
) -> BrowserRuntimeBundle:
    manifest = validate_runtime_manifest(payload.manifest)
    existing = (
        await session.execute(
            select(BrowserRuntimeBundle).where(
                BrowserRuntimeBundle.name == payload.manifest.name,
                BrowserRuntimeBundle.version == payload.manifest.version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise BrowserRuntimeError(
            "bundle_version_exists",
            f"runtime bundle {payload.manifest.name}@{payload.manifest.version} already exists",
        )
    bundle = BrowserRuntimeBundle(
        name=payload.manifest.name,
        version=payload.manifest.version,
        manifest=manifest,
        trust_level=payload.trust_level,
        source=payload.source,
    )
    session.add(bundle)
    await session.flush()
    return bundle


async def update_runtime_bundle(
    session: AsyncSession,
    bundle: BrowserRuntimeBundle,
    payload: BrowserRuntimeBundleCreate,
) -> BrowserRuntimeBundle:
    if bundle.trust_level == "system":
        raise BrowserRuntimeError(
            "system_bundle_immutable",
            "system runtime bundles are image-managed and cannot be edited",
        )
    if payload.manifest.name != bundle.name or payload.manifest.version != bundle.version:
        raise BrowserRuntimeError(
            "immutable_bundle_version",
            "bundle name and version are immutable; create a new bundle version instead",
        )
    in_use = (
        await session.execute(
            select(BrowserInstance.id).where(BrowserInstance.runtime_bundle_id == bundle.id)
        )
    ).first()
    if in_use is not None:
        raise BrowserRuntimeError(
            "bundle_in_use",
            "assigned runtime bundle versions are immutable; create a new version",
        )
    bundle.manifest = validate_runtime_manifest(payload.manifest)
    bundle.trust_level = payload.trust_level
    bundle.source = payload.source
    await session.flush()
    return bundle


async def delete_runtime_bundle(session: AsyncSession, bundle: BrowserRuntimeBundle) -> None:
    if bundle.trust_level == "system":
        raise BrowserRuntimeError(
            "system_bundle_immutable",
            "system runtime bundles are image-managed and cannot be deleted",
        )
    in_use = (
        await session.execute(
            select(BrowserInstance.id).where(BrowserInstance.runtime_bundle_id == bundle.id)
        )
    ).first()
    if in_use is not None:
        raise BrowserRuntimeError(
            "bundle_in_use", "cannot delete a runtime bundle assigned to a browser slot"
        )
    await session.delete(bundle)


async def list_browser_instances(session: AsyncSession) -> list[BrowserInstance]:
    result = await session.execute(select(BrowserInstance).order_by(BrowserInstance.endpoint))
    return list(result.scalars().all())


async def create_browser_instance(
    session: AsyncSession, payload: BrowserInstanceCreate
) -> BrowserInstance:
    if not payload.endpoint.startswith(("http://", "https://")):
        raise BrowserRuntimeError(
            "invalid_endpoint", "endpoint must start with http:// or https://"
        )
    if payload.runtime_bundle_id and not await get_runtime_bundle(
        session, payload.runtime_bundle_id
    ):
        raise BrowserRuntimeError("unknown_bundle", "selected runtime bundle does not exist")
    await _ensure_unique_profile(session, payload.profile_name)
    instance = BrowserInstance(**payload.model_dump())
    session.add(instance)
    await session.flush()
    return instance


async def update_browser_instance(
    session: AsyncSession,
    instance: BrowserInstance,
    payload: BrowserInstanceConfigUpdate,
) -> BrowserInstance:
    values = payload.model_dump(exclude_unset=True)
    if any(
        key in values and values[key] != getattr(instance, key)
        for key in ("profile_name", "profile_kind")
    ):
        from backend.services.browser_account_service import require_unassigned

        await require_unassigned(session, instance.id)
    if "runtime_bundle_id" in values and values["runtime_bundle_id"] is not None:
        if not await get_runtime_bundle(session, values["runtime_bundle_id"]):
            raise BrowserRuntimeError("unknown_bundle", "selected runtime bundle does not exist")
    if "profile_name" in values:
        await _ensure_unique_profile(
            session, values["profile_name"], excluding_instance_id=instance.id
        )
    runtime_configuration_changed = any(
        field in values and values[field] != getattr(instance, field)
        for field in (
            "runtime_bundle_id",
            "profile_name",
            "resource_class",
            "startup_pages",
            "network_policy",
        )
    )
    for field, value in values.items():
        setattr(instance, field, value)
    if runtime_configuration_changed:
        deployment = await get_runtime_deployment(session, instance.id)
        if deployment is not None:
            deployment.state = "RESTART_REQUIRED"
            deployment.diagnostics = [
                "Runtime Bundle or Profile changed; replace or restart the Slot before reuse"
            ]
    await session.flush()
    return instance


async def report_runtime_deployment(
    session: AsyncSession,
    instance: BrowserInstance,
    report: SlotRuntimeReport,
) -> BrowserRuntimeDeployment:
    bundle = (
        await get_runtime_bundle(session, instance.runtime_bundle_id)
        if instance.runtime_bundle_id
        else None
    )
    state, diagnostics = reconcile_runtime_state(bundle, report)
    deployment = (
        await session.execute(
            select(BrowserRuntimeDeployment).where(
                BrowserRuntimeDeployment.browser_instance_id == instance.id
            )
        )
    ).scalar_one_or_none()
    self_check = dict(report.self_check)
    self_check["capabilities"] = list(report.capabilities)
    if deployment is None:
        deployment = BrowserRuntimeDeployment(
            browser_instance_id=instance.id,
            loaded_bundle_name=report.loaded_bundle_name,
            loaded_bundle_version=report.loaded_bundle_version,
            loaded_components=[item.model_dump() for item in report.loaded_components],
            self_check=self_check,
            state=state,
            diagnostics=diagnostics,
        )
        session.add(deployment)
    else:
        deployment.loaded_bundle_name = report.loaded_bundle_name
        deployment.loaded_bundle_version = report.loaded_bundle_version
        deployment.loaded_components = [item.model_dump() for item in report.loaded_components]
        deployment.self_check = self_check
        deployment.state = state
        deployment.diagnostics = diagnostics
    await session.flush()
    return deployment


async def get_runtime_deployment(
    session: AsyncSession, instance_id: str
) -> BrowserRuntimeDeployment | None:
    return (
        await session.execute(
            select(BrowserRuntimeDeployment).where(
                BrowserRuntimeDeployment.browser_instance_id == instance_id
            )
        )
    ).scalar_one_or_none()
