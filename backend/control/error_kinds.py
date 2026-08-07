"""ErrorKind: a small, stable vocabulary for "what kind of failure was this run".

See docs/CONTROL_THEORY_ARCHITECTURE.md §2 principle 6 ("all failures must be
classified") and §0 (sensors must be honest). This module does NOT introduce a
new classification wheel — it maps the codebase's EXISTING structured
``error_type`` (``ChannelResult.fail(error_type=...)`` /
``ChannelFetchError.error_type``, already produced by every channel; see
``backend/channels/base.py``) and the existing retry taxonomy
(``backend/pipeline/error_taxonomy.py``) onto a coarser, control-facing kind.

Pure functions only: no I/O, no per-channel special-casing beyond the mapping
table below. v1 records exactly one terminal error per run (see
``backend.control.recorder``); a per-item error histogram is a future
extension noted there, not implemented here.
"""

from enum import Enum

from backend.pipeline import error_taxonomy


class ErrorKind(str, Enum):
    """Coarse, control-facing failure category. Deliberately smaller than the
    exception-class-name vocabulary in ``error_taxonomy`` — that module answers
    "retry or not"; this one answers "what should the controller DO about it"
    (pause vs backoff vs require_human_review, in a later PR-Control)."""

    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    NETWORK = "network"
    TIMEOUT = "timeout"
    SCHEMA_DRIFT = "schema_drift"
    VALIDATION = "validation"
    ODP_UNAVAILABLE = "odp_unavailable"
    STORE_FAILED = "store_failed"
    POISON_MESSAGE = "poison_message"
    # Human-cleared challenge wall (Doubao captcha) — the controller should
    # pause + require review, not backoff/retry on its own.
    CAPTCHA = "captcha"
    UNKNOWN = "unknown"


#: error_type (exception class name, or an explicit hint like
#: "SSRFValidationError"/"RetryableHTTPStatus"/"PermanentHTTPStatus") -> ErrorKind.
#: Reuses the exact strings already produced across the codebase — see
#: backend/pipeline/error_taxonomy.py's _RETRYABLE/_PERMANENT sets and the
#: explicit error_type values channels already set (rss_channel.py,
#: api_channel.py, etc.) — this table does not invent new ones.
_ERROR_TYPE_MAP: dict[str, ErrorKind] = {
    # Network / connectivity
    "ConnectError": ErrorKind.NETWORK,
    "ConnectionError": ErrorKind.NETWORK,
    "ReadError": ErrorKind.NETWORK,
    "WriteError": ErrorKind.NETWORK,
    "RemoteProtocolError": ErrorKind.NETWORK,
    "NetworkError": ErrorKind.NETWORK,
    "OSError": ErrorKind.NETWORK,
    # Timeout
    "TimeoutException": ErrorKind.TIMEOUT,
    "TimeoutError": ErrorKind.TIMEOUT,
    "ConnectTimeout": ErrorKind.TIMEOUT,
    "ReadTimeout": ErrorKind.TIMEOUT,
    "WriteTimeout": ErrorKind.TIMEOUT,
    "PoolTimeout": ErrorKind.TIMEOUT,
    # Rate limiting (explicit HTTP-status-derived hint set by channels/http_client)
    "RetryableHTTPStatus": ErrorKind.RATE_LIMITED,
    "RateLimitError": ErrorKind.RATE_LIMITED,
    "RateLimited": ErrorKind.RATE_LIMITED,
    # Auth
    "AuthenticationError": ErrorKind.AUTH_FAILED,
    "AuthFailed": ErrorKind.AUTH_FAILED,
    "Unauthorized": ErrorKind.AUTH_FAILED,
    "PermissionError": ErrorKind.AUTH_FAILED,
    # Schema / parsing drift (feed/DOM/API shape changed)
    "JSONDecodeError": ErrorKind.SCHEMA_DRIFT,
    "SchemaDriftError": ErrorKind.SCHEMA_DRIFT,
    "ParseError": ErrorKind.SCHEMA_DRIFT,
    # feedparser's bozo_exception class for malformed/truncated XML or an
    # encoding mismatch (rss_channel.py's bozo branch) — verified empirically
    # across markup corruption, truncated declarations, and encoding
    # mismatches; feedparser's strict parser wraps all of them the same way.
    "SAXParseException": ErrorKind.SCHEMA_DRIFT,
    # Validation / permanent bad input (including SSRF rejections — a
    # config/validation problem with the source, not a transient network fault)
    "ValueError": ErrorKind.VALIDATION,
    "KeyError": ErrorKind.VALIDATION,
    "TypeError": ErrorKind.VALIDATION,
    "ValidationError": ErrorKind.VALIDATION,
    "PermanentHTTPStatus": ErrorKind.VALIDATION,
    "SSRFValidationError": ErrorKind.VALIDATION,
    "FileNotFoundError": ErrorKind.VALIDATION,
    # ODP / store-layer specific
    "OdpUnavailableError": ErrorKind.ODP_UNAVAILABLE,
    "OdpIngestError": ErrorKind.ODP_UNAVAILABLE,
    "StoreError": ErrorKind.STORE_FAILED,
    "IntegrityError": ErrorKind.STORE_FAILED,
    "OperationalError": ErrorKind.STORE_FAILED,
    # Poison message (DLQ-bound: a message that will never succeed no matter
    # how many times it's retried)
    "PoisonMessageError": ErrorKind.POISON_MESSAGE,
    # Human-cleared challenge wall (doubao_research_channel's captcha
    # classification — see error_taxonomy.CAPTCHA_CHALLENGE)
    "captcha_challenge": ErrorKind.CAPTCHA,
}


def map_error_type(error_type: str | None) -> ErrorKind:
    """Map an existing structured ``error_type`` string to an ``ErrorKind``.

    Pure lookup — no string parsing of free-text error messages. An
    ``error_type`` not in the table (including ``None``) maps to
    ``ErrorKind.UNKNOWN`` rather than guessing.
    """
    if not error_type:
        return ErrorKind.UNKNOWN
    return _ERROR_TYPE_MAP.get(error_type, ErrorKind.UNKNOWN)


def map_exception(exc: BaseException | None) -> ErrorKind:
    """Map a live exception to an ``ErrorKind`` via
    ``error_taxonomy.effective_error_type`` (which already prefers
    ``ChannelFetchError.error_type`` / unwraps ``__cause__`` over the wrapper's
    own class name) — so this stays consistent with the retry classification
    the pipeline already applied to the same exception."""
    if exc is None:
        return ErrorKind.UNKNOWN
    return map_error_type(error_taxonomy.effective_error_type(exc))
