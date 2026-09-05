"""Dedicated managed runtime fixtures for Space contract tests."""

from backend.models.browser import BrowserInstance, BrowserRuntimeBundle, BrowserRuntimeDeployment


async def configure_space_runtime(db, instance: BrowserInstance):
    bundle = BrowserRuntimeBundle(
        name=f"space-{instance.id}",
        version="1",
        manifest={
            "name": "space-test",
            "version": "1",
            "components": [
                {
                    "kind": "script",
                    "id": "page",
                    "version": "1",
                    "path": "scripts/page",
                    "capabilities": ["snapshot"],
                }
            ],
            "capabilities": [
                {
                    "name": "snapshot",
                    "component_id": "page",
                    "action": "snapshot",
                    "args_schema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "additionalProperties": False,
                    },
                }
            ],
        },
    )
    db.add(bundle)
    await db.flush()
    instance.runtime_bundle_id = bundle.id
    instance.agent_url = instance.endpoint
    instance.agent_protocol = "http"
    db.add(
        BrowserRuntimeDeployment(
            browser_instance_id=instance.id,
            state="READY",
            loaded_bundle_name=bundle.name,
            loaded_bundle_version="1",
            loaded_components=[{"id": "page", "healthy": True}],
        )
    )
    await db.commit()
    return bundle
