"""M0: III worker that posts Record v2 events to Rust odp-ingest."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
from iii_observability import Logger

from iii import InitOptions, register_worker

III_ROOT = Path(__file__).resolve().parents[2]
if str(III_ROOT) not in sys.path:
    sys.path.insert(0, str(III_ROOT))

from lib.env_bootstrap import bootstrap_worker_env  # noqa: E402
from lib.odp_record import post_batch_sync  # noqa: E402

bootstrap_worker_env()

worker = register_worker(
    os.environ.get("III_URL", "ws://localhost:49134"),
    InitOptions(
        worker_name="odp-ingest-bridge",
        worker_description="Forwards Record v2 batches to Rust odp-ingest (ODP data plane)",
    ),
)
logger = Logger()


def _admin_callback_url(resource: str) -> str:
    lifecycle_url = os.environ.get("ADMIN_III_LIFECYCLE_URL", "").strip().rstrip("/")
    if not lifecycle_url or not lifecycle_url.endswith("/lifecycle"):
        raise RuntimeError("ADMIN_III_LIFECYCLE_URL must name the Admin lifecycle endpoint")
    return f"{lifecycle_url.rsplit('/', 1)[0]}/{resource}"


def _emit_ingress_receipt(receipt: dict[str, Any]) -> None:
    headers = {"content-type": "application/json"}
    fleet_token = os.environ.get("API_AUTH_TOKEN", "")
    if fleet_token:
        headers["authorization"] = f"Bearer {fleet_token}"
    bridge_token = os.environ.get("ADMIN_III_LIFECYCLE_TOKEN", "")
    if bridge_token:
        headers["x-iii-bridge-token"] = bridge_token
    try:
        response = httpx.post(
            _admin_callback_url("ingress-receipts"), json=receipt, headers=headers, timeout=10.0
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError("Admin ingress receipt callback failed") from exc


def batch_handler(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events") or []
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    trace_id = payload.get("trace_id")
    task_id = payload.get("task_id")
    if trace_id or task_id:
        patched: list[dict[str, Any]] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            row = dict(ev)
            if trace_id and not row.get("trace_id"):
                row["trace_id"] = trace_id
            if task_id and not row.get("task_id"):
                row["task_id"] = task_id
            patched.append(row)
        events = patched
    metadata = payload.get("admin_collection")
    receipt_context: dict[str, Any] | None = None
    if metadata is not None:
        if not isinstance(metadata, dict) or not isinstance(
            metadata.get("expected_key_set_sha256"), str
        ):
            raise ValueError("governed ingress requires immutable receipt context")
        receipt_context = dict(metadata)

    logger.info("odp.ingest::batch", {"count": len(events)})
    result = post_batch_sync(events, receipt_context=receipt_context)
    if receipt_context is not None:
        receipt = result.pop("ingress_receipt", None)
        if not isinstance(receipt, dict):
            raise RuntimeError("odp-ingest did not produce a signed ingress receipt")
        _emit_ingress_receipt(receipt)
    return {"ok": True, **result}


def single_handler(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    return batch_handler(
        {
            "events": [event],
            "trace_id": payload.get("trace_id"),
            "task_id": payload.get("task_id"),
        }
    )


def health_handler(_payload: dict[str, Any]) -> dict[str, Any]:
    from lib.odp_record import ingest_base_url

    return {"ok": True, "ingest_url": ingest_base_url()}


worker.register_function(
    "odp.ingest::batch",
    batch_handler,
    description="POST a Record v2 batch to Rust odp-ingest",
)
worker.register_function(
    "odp.ingest::single",
    single_handler,
    description="POST one Record v2 event to Rust odp-ingest",
)
worker.register_function(
    "odp.ingest::health",
    health_handler,
    description="Return configured ODP_INGEST_URL (connectivity probe)",
)

print("odp-ingest-bridge started — odp.ingest::{batch,single,health}")
