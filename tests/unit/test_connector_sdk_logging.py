"""The fixed Lark SDK logger cannot leak connector diagnostics."""

from __future__ import annotations

import io
import logging

import pytest

from backend.services.connector_sdk_logging import configure_connector_sdk_logging

SDK_LOGGER_NAME = "Lark"


@pytest.fixture
def sdk_logger_state():
    import lark_channel.core.log as sdk_log

    logger = logging.getLogger(SDK_LOGGER_NAME)
    handlers = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    disabled = logger.disabled
    yield logger, sdk_log
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    for handler in handlers:
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = propagate
    logger.disabled = disabled


def test_sdk_handler_leaks_before_helper_and_is_silent_afterwards(sdk_logger_state):
    logger, sdk_log = sdk_logger_state
    marker = "bot_identity_raw-marker-open-chat-token"
    logger.disabled = False
    logger.propagate = True
    logger.setLevel(logging.WARNING)

    stream = io.StringIO()
    original_streams = []
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            original_streams.append((handler, handler.stream))
            handler.setStream(stream)
    assert original_streams, "the installed SDK must provide its stdout handler"
    logger.warning("provider failure: %s", marker)
    assert marker in stream.getvalue()

    configure_connector_sdk_logging()
    configure_connector_sdk_logging()
    assert logger.handlers == []
    assert logger.propagate is False
    assert logger.disabled is True
    stream.seek(0)
    stream.truncate(0)
    logger.warning("provider failure after helper: %s", marker)
    assert stream.getvalue() == ""
    assert sdk_log.logger is logger


@pytest.mark.asyncio
async def test_actual_sdk_identity_and_send_errors_stay_silent_after_client_level_reset(
    sdk_logger_state, monkeypatch
):
    logger, _sdk_log = sdk_logger_state
    configure_connector_sdk_logging()

    from lark_channel import FeishuChannel, OutboundText
    from lark_channel.channel import bot_identity
    from lark_channel.core.enum import LogLevel

    marker = "raw-bot-identity-send-error-open-chat-token"

    async def fail_transport(*_args, **_kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(bot_identity.Transport, "aexecute", fail_transport)
    channel = FeishuChannel(
        app_id="cli-sdk-logging-test",
        app_secret="app-secret-sdk-logging-test",
        log_level=LogLevel.DEBUG,
    )

    # Client construction calls the SDK's _init_logger and changes its level;
    # the helper's disabled boundary must still hold.
    assert logger.level == logging.DEBUG
    assert logger.disabled is True
    assert await channel.resolve_bot_identity() is None

    async def fail_create(*_args, **_kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(channel.client.im.v1.message, "acreate", fail_create)
    result = await channel.sender.send(
        OutboundText(text="safe connector message"),
        receive_id="oc-safe-chat",
        receive_id_type="chat_id",
    )
    assert result.success is False
    assert result.error is not None


def test_application_errors_remain_visible_and_sdk_state_is_isolated(sdk_logger_state):
    logger, _sdk_log = sdk_logger_state
    configure_connector_sdk_logging()

    root = logging.getLogger()
    root_level = root.level
    capture = logging.Handler()
    records: list[str] = []
    capture.emit = lambda record: records.append(record.getMessage())
    root.addHandler(capture)
    root.setLevel(logging.ERROR)
    try:
        app_logger = logging.getLogger("backend.services.connector_reply_worker")
        app_logger.error("stable connector worker error")
        logger.error("raw sdk marker must stay hidden")
    finally:
        root.removeHandler(capture)
        root.setLevel(root_level)

    assert records == ["stable connector worker error"]
    assert logger.handlers == []
    assert logger.propagate is False
