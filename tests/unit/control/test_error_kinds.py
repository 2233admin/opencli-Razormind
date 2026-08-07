"""backend.control.error_kinds: pure mapping from the EXISTING structured
error_type/taxonomy onto a small control-facing ErrorKind vocabulary.

No string parsing of error_message anywhere here — only exact error_type
lookups, mirroring how backend/pipeline/error_taxonomy.py already classifies
retryability from the same strings.
"""

from backend.control.error_kinds import ErrorKind, map_error_type, map_exception


class TestMapErrorType:
    def test_none_maps_to_unknown(self):
        assert map_error_type(None) is ErrorKind.UNKNOWN

    def test_empty_string_maps_to_unknown(self):
        assert map_error_type("") is ErrorKind.UNKNOWN

    def test_unrecognized_type_maps_to_unknown(self):
        assert map_error_type("SomeBrandNewException") is ErrorKind.UNKNOWN

    def test_timeout_types(self):
        for t in ("TimeoutException", "TimeoutError", "ConnectTimeout", "ReadTimeout", "PoolTimeout"):
            assert map_error_type(t) is ErrorKind.TIMEOUT

    def test_network_types(self):
        for t in ("ConnectError", "ConnectionError", "ReadError", "OSError", "NetworkError"):
            assert map_error_type(t) is ErrorKind.NETWORK

    def test_rate_limited(self):
        assert map_error_type("RetryableHTTPStatus") is ErrorKind.RATE_LIMITED

    def test_validation_types_including_ssrf(self):
        for t in ("ValueError", "ValidationError", "PermanentHTTPStatus", "SSRFValidationError"):
            assert map_error_type(t) is ErrorKind.VALIDATION

    def test_schema_drift(self):
        # WIRING_GAP_LEDGER W1: SAXParseException is feedparser's real
        # bozo_exception class for a malformed RSS/Atom feed (rss_channel.py's
        # bozo branch) -- verified empirically across markup corruption,
        # truncated declarations, and encoding mismatches.
        for t in ("JSONDecodeError", "SchemaDriftError", "ParseError", "SAXParseException"):
            assert map_error_type(t) is ErrorKind.SCHEMA_DRIFT

    def test_store_failed(self):
        assert map_error_type("IntegrityError") is ErrorKind.STORE_FAILED

    def test_captcha_challenge_maps_to_captcha(self):
        assert map_error_type("captcha_challenge") is ErrorKind.CAPTCHA


class TestMapException:
    def test_none_maps_to_unknown(self):
        assert map_exception(None) is ErrorKind.UNKNOWN

    def test_plain_timeout_exception(self):
        assert map_exception(TimeoutError("slow")) is ErrorKind.TIMEOUT

    def test_channel_fetch_error_prefers_explicit_error_type(self):
        from backend.channels.base import ChannelFetchError

        exc = ChannelFetchError("boom", error_type="SSRFValidationError")
        assert map_exception(exc) is ErrorKind.VALIDATION

    def test_channel_fetch_error_unwraps_cause_when_no_explicit_type(self):
        from backend.channels.base import ChannelFetchError

        try:
            try:
                raise ConnectionError("dial tcp: refused")
            except ConnectionError as inner:
                raise ChannelFetchError("wrapped") from inner
        except ChannelFetchError as exc:
            assert map_exception(exc) is ErrorKind.NETWORK

    def test_unmapped_exception_class_is_unknown(self):
        class WeirdCustomError(Exception):
            pass

        assert map_exception(WeirdCustomError("???")) is ErrorKind.UNKNOWN
