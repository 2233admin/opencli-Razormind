"""M2+M3: Register III cron triggers from schedules/*.yaml."""

from __future__ import annotations

import os
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from iii.triggers import Trigger
from iii_observability import Logger

from iii import InitOptions, register_worker

III_ROOT = Path(__file__).resolve().parents[3]
if str(III_ROOT) not in sys.path:
    sys.path.insert(0, str(III_ROOT))

from lib.env_bootstrap import bootstrap_worker_env  # noqa: E402
from lib.schedules import (  # noqa: E402
    DEFAULT_OPENCLI_SCHEDULES_PATH,
    DEFAULT_SCHEDULES_PATH,
    load_discord_schedules,
    load_opencli_schedules,
)

bootstrap_worker_env()

worker = register_worker(
    os.environ.get("III_URL", "ws://localhost:49134"),
    InitOptions(
        worker_name="schedule-bootstrap",
        worker_description="Registers cron triggers for Discord + OpenCLI collection",
    ),
)
logger = Logger()

_registered: dict[str, Trigger] = {}
_function_ids: set[str] = set()


def _function_id(kind: str, schedule_id: str) -> str:
    return f"odp.schedule::{kind}/{schedule_id}"


def _task_id_for_schedule(kind: str, schedule_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opencli-admin/schedule/{kind}/{schedule_id}"))


def _make_discord_runner(schedule: dict[str, Any]):
    def handler(cron_event: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "channel_id": schedule["channel_id"],
            "limit": schedule["limit"],
            "task_id": _task_id_for_schedule("discord", schedule["id"]),
            "trace_id": str(uuid.uuid4()),
            "schedule_id": schedule["id"],
            "cron": cron_event,
        }
        if schedule.get("source_id"):
            payload["source_id"] = schedule["source_id"]
        if schedule.get("channel_name"):
            payload["channel_name"] = schedule["channel_name"]
        logger.info(
            "odp.schedule::discord tick",
            {"schedule_id": schedule["id"], "channel_id": schedule["channel_id"]},
        )
        return worker.trigger(
            {"function_id": "odp.collect::discord_snapshot", "payload": payload}
        )

    return handler


def _make_opencli_runner(schedule: dict[str, Any]):
    def handler(cron_event: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "site": schedule["site"],
            "command": schedule["command"],
            "args": schedule.get("args") or {},
            "positional_args": schedule.get("positional_args") or [],
            "format": schedule.get("format") or "json",
            "task_id": _task_id_for_schedule("opencli", schedule["id"]),
            "trace_id": str(uuid.uuid4()),
            "schedule_id": schedule["id"],
            "cron": cron_event,
        }
        if schedule.get("source_id"):
            payload["source_id"] = schedule["source_id"]
        if schedule.get("mode"):
            payload["mode"] = schedule["mode"]
        logger.info(
            "odp.schedule::opencli tick",
            {
                "schedule_id": schedule["id"],
                "site": schedule["site"],
                "command": schedule["command"],
            },
        )
        return worker.trigger(
            {"function_id": "odp.collect::opencli_snapshot", "payload": payload}
        )

    return handler


def _register_schedule(
    kind: str,
    schedule: dict[str, Any],
    runner_factory: Callable[[dict[str, Any]], Callable[[dict[str, Any]], dict[str, Any]]],
) -> None:
    sid = schedule["id"]
    key = f"{kind}:{sid}"
    if key in _registered:
        return

    fn_id = _function_id(kind, sid)
    if fn_id not in _function_ids:
        worker.register_function(
            fn_id,
            runner_factory(schedule),
            description=f"Cron wrapper → {kind} snapshot for schedule {sid}",
        )
        _function_ids.add(fn_id)

    trigger = worker.register_trigger(
        {
            "type": "cron",
            "function_id": fn_id,
            "config": {"expression": schedule["expression"]},
            "metadata": {"schedule_id": sid, "kind": kind},
        }
    )
    _registered[key] = trigger
    logger.info(
        "cron registered",
        {
            "kind": kind,
            "schedule_id": sid,
            "function_id": fn_id,
            "expression": schedule["expression"],
        },
    )


def bootstrap_schedules() -> dict[str, Any]:
    discord_path = os.environ.get("DISCORD_SCHEDULES_PATH", str(DEFAULT_SCHEDULES_PATH))
    opencli_path = os.environ.get("OPENCLI_SCHEDULES_PATH", str(DEFAULT_OPENCLI_SCHEDULES_PATH))

    for sched in load_discord_schedules(discord_path):
        _register_schedule("discord", sched, _make_discord_runner)
    for sched in load_opencli_schedules(opencli_path):
        _register_schedule("opencli", sched, _make_opencli_runner)

    return {
        "ok": True,
        "discord_schedules_path": discord_path,
        "opencli_schedules_path": opencli_path,
        "registered": list(_registered.keys()),
        "count": len(_registered),
    }


def list_schedules_handler(_payload: dict[str, Any]) -> dict[str, Any]:
    discord_path = os.environ.get("DISCORD_SCHEDULES_PATH", str(DEFAULT_SCHEDULES_PATH))
    opencli_path = os.environ.get("OPENCLI_SCHEDULES_PATH", str(DEFAULT_OPENCLI_SCHEDULES_PATH))
    return {
        "discord_schedules_path": discord_path,
        "opencli_schedules_path": opencli_path,
        "discord_configured": load_discord_schedules(discord_path),
        "opencli_configured": load_opencli_schedules(opencli_path),
        "registered": list(_registered.keys()),
    }


def reload_schedules_handler(_payload: dict[str, Any]) -> dict[str, Any]:
    for key, trigger in list(_registered.items()):
        trigger.unregister()
        _registered.pop(key, None)
    return bootstrap_schedules()


worker.register_function(
    "odp.schedule::bootstrap",
    bootstrap_schedules,
    description="Register cron triggers from schedules/discord.yaml + opencli.yaml",
)
worker.register_function(
    "odp.schedule::list",
    list_schedules_handler,
    description="List configured and registered Discord + OpenCLI schedules",
)
worker.register_function(
    "odp.schedule::reload",
    reload_schedules_handler,
    description="Unregister and re-register all cron schedules",
)

time.sleep(1)
result = bootstrap_schedules()
print(
    "schedule-bootstrap started —",
    f"{result['count']} cron trigger(s)",
)
