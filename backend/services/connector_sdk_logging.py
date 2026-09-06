"""Keep connector SDK diagnostics inside the connector trust boundary.

The installed Lark Channel SDK configures a logger named ``Lark`` with a
stdout handler and changes its level while constructing a client.  Connector
credentials, remote identifiers, and provider exception text can therefore
escape through that logger before the application has a chance to redact
them.  This module is intentionally stdlib-only so both connector adapters
can apply the same boundary after importing the SDK.
"""

from __future__ import annotations

import logging

_SDK_LOGGER_NAME = "Lark"
_DISABLED_LEVEL = logging.CRITICAL + 1


def configure_connector_sdk_logging() -> None:
    """Silence raw diagnostics emitted by the Lark Channel SDK.

    The SDK logger is isolated from application handlers and disabled, so a
    later SDK ``setLevel`` call cannot re-enable its output.  Removing the
    handlers also prevents the SDK's own stdout handler from bypassing the
    application's logging policy.  The operation is deliberately idempotent.
    """

    sdk_logger = logging.getLogger(_SDK_LOGGER_NAME)
    for handler in list(sdk_logger.handlers):
        sdk_logger.removeHandler(handler)
    sdk_logger.propagate = False
    sdk_logger.setLevel(_DISABLED_LEVEL)
    sdk_logger.disabled = True


__all__ = ["configure_connector_sdk_logging"]
