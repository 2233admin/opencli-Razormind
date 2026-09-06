"""Only the isolated E2E runner imports this in-memory SDK outbound fixture."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

STUDIO_WORKSPACE_ID = "openalice-e2e-studio"
INSTALLATION_ID = "openalice-p2-connector"
APP_ID = "cli_e2e_delivery"
TENANT_KEY = "tenant_e2e_delivery"
ENCRYPT_KEY = "dummy-e2e-delivery-encrypt-key"
VERIFICATION_TOKEN = "dummy-e2e-delivery-verification-token"
CHAT_ID = "oc-e2e-delivery"


async def seed_delivery_installation(db_path: Path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.models.connector_reply import ConnectorInstallation
    from tests.fixtures.openalice_workspace.seed import WORKSPACE_ID

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        installation = ConnectorInstallation(
            public_id=INSTALLATION_ID,
            workspace_id=WORKSPACE_ID,
            provider="feishu",
            name="本地验收飞书机器人",
            app_id=APP_ID,
            tenant_key=TENANT_KEY,
            status="active",
        )
        installation.app_secret = "dummy-e2e-delivery-app-secret"
        installation.encrypt_key = ENCRYPT_KEY
        installation.verification_token = VERIFICATION_TOKEN
        db.add(installation)
        await db.commit()
    await engine.dispose()


def configure_delivery_app(app: FastAPI) -> None:
    """Keep real routes/DB/worker/inbound; replace only outbound network calls."""

    import lark_channel

    from backend.main import connector_reply_lifespan

    sent: list[dict[str, object]] = []

    class FixtureChannel:
        def __init__(self, **credentials):
            if credentials.get("app_id") != APP_ID:
                raise ValueError("unexpected fixture installation")

        async def connect_until_ready(self, *, timeout):
            del timeout

        async def send(self, chat_id, message, options):
            if chat_id != CHAT_ID:
                raise ValueError("unexpected fixture chat")
            message_id = f"om_e2e_{options['uuid'].replace('-', '')}"
            if isinstance(message, str):
                text = message
            elif isinstance(message, lark_channel.OutboundText):
                text = message.text
            elif isinstance(message, lark_channel.OutboundFile):
                if message.source is None or message.source.kind != "buffer":
                    raise ValueError("fixture accepts only in-memory files")
                text = (message.source.buffer or b"").decode("utf-8")
            else:
                raise ValueError("unsupported fixture message")
            sent.append(
                {
                    "message_id": message_id,
                    "chat_id": chat_id,
                    "text": text,
                    "uuid": options["uuid"],
                    "reply_to": options.get("reply_to"),
                }
            )
            return lark_channel.SendResult(success=True, message_id=message_id)

        async def disconnect(self):
            return None

    lark_channel.FeishuChannel = FixtureChannel
    # Do not start application business migrations, schedulers, or collectors.
    # This is the exact production connector context, with fixture-only settings.
    app.router.lifespan_context = connector_reply_lifespan

    @app.get("/api/__test__/connector-deliveries", include_in_schema=False)
    async def inspect_fixture_deliveries(request: Request):
        if (
            request.client is None
            or request.client.host not in {"127.0.0.1", "::1"}
            or request.headers.get("authorization") != "Bearer openalice-e2e-token"
        ):
            raise HTTPException(403, "isolated fixture access required")
        # An observer of the fake remote recipient, never a product endpoint.
        # The capture lives in this disposable process only.
        return {"data": sent[-100:]}

    # create_app ends with the root MCP mount; the fixture observer must precede it.
    # Middleware still applies, and no production route or mount is removed.
    app.router.routes.insert(0, app.router.routes.pop())
