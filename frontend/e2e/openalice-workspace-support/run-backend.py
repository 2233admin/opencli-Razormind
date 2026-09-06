"""Start the isolated OpenAlice acceptance backend with a deterministic chat seam."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def configure_environment(db_path: Path) -> None:
    from cryptography.fernet import Fernet

    connector_enabled = os.environ.get("OPENALICE_CONNECTOR_P2_E2E") == "1"
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}",
            "API_AUTH_TOKEN": "",
            "BOOTSTRAP_ADMIN_TOKEN": "openalice-e2e-token",
            "SECRET_KEY": "openalice-e2e-secret-key-012345678901234567890123",
            "CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "CONNECTOR_REPLY_ENABLED": str(connector_enabled).lower(),
            "CONNECTOR_ARTIFACT_DELIVERY_ENABLED": str(connector_enabled).lower(),
            "WORKFLOW_PLUGINS": "",
            "TASK_EXECUTOR": "local",
            "AGENT_CONVERSATION_EXECUTION_MODE": (
                "local_single_process" if os.environ.get("OPENALICE_CHAT_E2E") == "1"
                else os.environ.get("AGENT_CONVERSATION_EXECUTION_MODE", "disabled")
            ),
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
        tool_trace.append(
            {"name": "e2e_deterministic_reply", "kind": "read", "status": "completed"}
        )
    from backend.api.v1 import chat
    from backend.schemas.common import ApiResponse

    latest = body.messages[-1].content if body.messages else ""
    if os.environ.get("OPENALICE_CHAT_E2E") == "1":
        await chat._emit_activity("tool.started", "读取会话资料", "测试模型边界正在处理本轮输入")
        if "[slow]" in latest:
            # Slow only the model seam to permit a real browser disconnect.
            await asyncio.sleep(6)
        await chat._emit_activity("tool.completed", "资料读取完成", "本轮工具处理完成")
        return ApiResponse.ok(chat.ChatReply(
            type="message",
            content=f"E2E reply ({len(body.messages)} messages, {body.model_id}): {latest}",
        ))
    return ApiResponse.ok(
        chat.ChatReply(type="message", content=f"Deterministic E2E reply: {latest}")
    )


async def seed_chat_target() -> None:
    """Seed only the isolated SQLite acceptance DB with a selectable provider."""
    from backend.database import AsyncSessionLocal
    from backend.models.provider import ModelProvider
    from backend.models.provider_model import ProviderModel

    async with AsyncSessionLocal() as session:
        provider = ModelProvider(
            name="Deterministic chat provider",
            provider_type="openai",
            base_url="http://deterministic.invalid/v1",
            api_key=None,
            default_model="deterministic-chat",
            enabled=True,
        )
        session.add(provider)
        await session.flush()
        session.add(ProviderModel(
            provider_id=provider.id,
            model_id="deterministic-chat",
            model_type="llm",
            capabilities={"tools": True},
            source="manual",
            enabled=True,
        ))
        await session.commit()


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

    from tests.fixtures.openalice_workspace.seed import WORKSPACE_ID, seed_database

    args.db.parent.mkdir(parents=True, exist_ok=True)
    connector_delivery = os.environ.get("OPENALICE_CONNECTOR_P2_E2E") == "1"
    studio_workspace_id = (
        "openalice-e2e-studio"
        if connector_delivery or os.environ.get("OPENALICE_CHAT_E2E") == "1"
        else WORKSPACE_ID
    )
    asyncio.run(seed_database(args.db, studio_workspace_id=studio_workspace_id))
    if os.environ.get("OPENALICE_CHAT_E2E") == "1":
        asyncio.run(seed_chat_target())
    if os.environ.get("OPENALICE_CONNECTOR_E2E") == "1":
        from tests.fixtures.openalice_workspace.connectors import seed_connector_scopes

        asyncio.run(seed_connector_scopes(args.db))
    if connector_delivery:
        from tests.fixtures.openalice_workspace.connector_delivery import (
            seed_delivery_installation,
        )

        asyncio.run(seed_delivery_installation(args.db))

    from backend.api.v1 import chat

    chat.run_chat_request = deterministic_chat

    import uvicorn

    application = "backend.main:app"
    if connector_delivery:
        from backend.main import app
        from tests.fixtures.openalice_workspace.connector_delivery import configure_delivery_app

        configure_delivery_app(app)
        application = app

    uvicorn.run(
        application,
        host="127.0.0.1",
        port=args.port,
        lifespan="on" if connector_delivery else "off",
        log_level="warning",
    )


if __name__ == "__main__":
    main()
