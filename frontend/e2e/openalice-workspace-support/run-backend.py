"""Start the isolated OpenAlice acceptance backend with a deterministic chat seam."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def configure_environment(db_path: Path) -> None:
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}",
            "API_AUTH_TOKEN": "",
            "BOOTSTRAP_ADMIN_TOKEN": "openalice-e2e-token",
            "SECRET_KEY": "openalice-e2e-secret-key-012345678901234567890123",
            "CREDENTIAL_ENCRYPTION_KEY": "",
            "WORKFLOW_PLUGINS": "",
            "TASK_EXECUTOR": "local",
        }
    )


async def deterministic_chat(
    _model_db,
    body,
    _request_identity,
    *,
    tool_trace=None,
    proposal_provenance=None,
):
    """Replace only the existing chat executor boundary for this test server."""

    del proposal_provenance
    if tool_trace is not None:
        tool_trace.append({"name": "e2e_deterministic_reply", "kind": "read", "status": "completed"})
    from backend.api.v1 import chat
    from backend.schemas.common import ApiResponse

    latest = body.messages[-1].content if body.messages else ""
    return ApiResponse.ok(
        chat.ChatReply(type="message", content=f"Deterministic E2E reply: {latest}")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--port", default=8048, type=int)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[3]
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    configure_environment(args.db)

    from tests.fixtures.openalice_workspace.seed import seed_database

    args.db.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(seed_database(args.db))

    from backend.api.v1 import chat

    chat.run_chat_request = deterministic_chat

    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=args.port,
        lifespan="off",
        log_level="warning",
    )


if __name__ == "__main__":
    main()
