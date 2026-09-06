"""The fixed Lark SDK logger cannot leak connector diagnostics."""

from __future__ import annotations

import io
import logging
import sys

import pytest

from backend.services.connector_sdk_logging import configure_connector_sdk_logging

SDK_LOGGER_NAME = "Lark"


def _usable_stream(stream):
    if not getattr(stream, "closed", False):
        return stream
    current = sys.stdout
    if not getattr(current, "closed", False):
        return current
    return sys.__stdout__


def _restore_stream(handler, stream):
    target = _usable_stream(stream)
    if getattr(handler.stream, "closed", False):
        # StreamHandler.setStream flushes the previous stream first. Assigning
        # directly avoids flushing a capture stream already closed by pytest.
        handler.stream = target
    else:
        handler.setStream(target)


@pytest.fixture
def sdk_logger_state():
    import lark_channel.core.log as sdk_log

    logger = logging.getLogger(SDK_LOGGER_NAME)
    handlers = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    disabled = logger.disabled
    sdk_handler = sdk_log.handler
    if sdk_handler not in logger.handlers:
        # A preceding integration test may already have applied the production
        # helper. Reattach the installed SDK handler for this test only.
        logger.addHandler(sdk_handler)
    test_handlers = list(logger.handlers)
    test_streams = {
        handler: _usable_stream(handler.stream)
        for handler in test_handlers
        if isinstance(handler, logging.StreamHandler)
    }
    for handler, stream in test_streams.items():
        if handler.stream is not stream:
            _restore_stream(handler, stream)
    try:
        yield logger, sdk_log
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        for handler, stream in test_streams.items():
            _restore_stream(handler, stream)
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
    try:
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
    finally:
        for handler, original_stream in original_streams:
            _restore_stream(handler, original_stream)


@pytest.mark.asyncio
async def test_actual_sdk_identity_and_send_errors_stay_silent_after_client_level_reset(
    sdk_logger_state, monkeypatch, capfd, caplog
):
    logger, _sdk_log = sdk_logger_state
    configure_connector_sdk_logging()

    from lark_channel import FeishuChannel, OutboundText
    from lark_channel.channel import bot_identity
    from lark_channel.core.enum import LogLevel

    marker = "raw-bot-identity-send-error-open-chat-token"

    calls = {"identity": 0, "create": 0}

    def fail_transport(*_args, **_kwargs):
        calls["identity"] += 1
        raise RuntimeError(marker)

    async def fail_async_transport(*_args, **_kwargs):
        calls["identity"] += 1
        raise RuntimeError(marker)

    monkeypatch.setattr(bot_identity.Transport, "execute", fail_transport)
    monkeypatch.setattr(bot_identity.Transport, "aexecute", fail_async_transport)
    channel = FeishuChannel(
        app_id="cli-sdk-logging-test",
        app_secret="app-secret-sdk-logging-test",
        log_level=LogLevel.DEBUG,
    )

    # Client construction calls the SDK's _init_logger and changes its level;
    # the helper's disabled boundary must still hold.
    assert logger.level == logging.DEBUG
    assert logger.disabled is True
    caplog.set_level(logging.DEBUG)
    assert await channel.resolve_bot_identity() is None
    assert calls["identity"] >= 2

    async def fail_create(*_args, **_kwargs):
        calls["create"] += 1
        raise RuntimeError(marker)

    monkeypatch.setattr(channel.client.im.v1.message, "acreate", fail_create)
    result = await channel.sender.send(
        OutboundText(text="safe connector message"),
        receive_id="oc-safe-chat",
        receive_id_type="chat_id",
    )
    assert result.success is False
    assert result.error is not None
    assert calls["create"] >= 1
    captured = capfd.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err
    assert marker not in caplog.text


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
