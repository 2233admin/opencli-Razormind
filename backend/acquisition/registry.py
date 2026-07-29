"""Versioned managed-acquisition capabilities backed by real OpenCLI commands."""

from dataclasses import dataclass

OHMYOPENCLI_COMMIT = "bfe1c25b4b12661058dd6e9980c562a09f230cc7"
OFFICIAL_SITE_CAPABILITY_COMMIT = "73cc60c83586ef2c95469b3b70d6cfc80fa5bc53"
DOUBAO_CAPABILITY_COMMIT = "bfe1c25b4b12661058dd6e9980c562a09f230cc7"
OPENCLI_VERSION = "1.8.5"


@dataclass(frozen=True)
class CapabilityRegistration:
    capability_id: str
    capability_version: str
    output_schema_version: str
    source_commit: str
    invocation: dict[str, str]
    probe_args: tuple[str, ...]
    help_marker: str
    route_probe_args: tuple[str, ...]
    route_probe_error: str
    required_profile_kind: str = "anonymous"
    url_input_field: str | None = "url"
    target: str | None = None
    session_probe_args: tuple[str, ...] = ()
    session_unavailable_reason: str | None = None
    session_expected_host: str | None = None

    @property
    def identity(self) -> tuple[str, str, str, str | None]:
        return (
            self.capability_id,
            self.capability_version,
            self.output_schema_version,
            self.target,
        )

    def runtime_identity(self) -> dict[str, str]:
        return {
            "ohmyopencli_repo_commit": OHMYOPENCLI_COMMIT,
            "capability_source_commit": self.source_commit,
            "opencli_version": OPENCLI_VERSION,
        }


_REGISTRATIONS = (
    CapabilityRegistration(
        capability_id="official-site.observe",
        capability_version="1.0.0",
        output_schema_version="1",
        source_commit=OFFICIAL_SITE_CAPABILITY_COMMIT,
        invocation={
            "site": "official-site",
            "command": "observe",
            "format": "json",
        },
        probe_args=("official-site", "observe", "--help"),
        help_marker="official-site observe",
        route_probe_args=(
            "official-site",
            "observe",
            "--url",
            "https://example.invalid",
            "-f",
            "json",
        ),
        route_probe_error="CDP not reachable at http://127.0.0.1:9",
    ),
    CapabilityRegistration(
        capability_id="chat-ai.capture",
        capability_version="1.0.0",
        output_schema_version="1",
        source_commit=DOUBAO_CAPABILITY_COMMIT,
        invocation={
            "site": "doubao",
            "command": "capture",
            "format": "json",
        },
        probe_args=("doubao", "capture", "--help"),
        help_marker="doubao capture",
        route_probe_args=(
            "doubao",
            "capture",
            "runtime-route-probe",
            "-f",
            "json",
        ),
        route_probe_error="CDP not reachable at http://127.0.0.1:9",
        required_profile_kind="authenticated",
        url_input_field=None,
        target="doubao",
        session_probe_args=(
            "doubao",
            "session-probe",
            "--strict",
            "true",
            "-f",
            "json",
        ),
        session_unavailable_reason="doubao_session_not_ready",
        session_expected_host="www.doubao.com",
    ),
)


def list_capability_registrations() -> tuple[CapabilityRegistration, ...]:
    """Return the audited capabilities that Admin is allowed to dispatch."""
    return _REGISTRATIONS


def get_capability_registration(
    capability_id: str,
    capability_version: str,
    output_schema_version: str,
    target: str | None = None,
) -> CapabilityRegistration | None:
    identity = (capability_id, capability_version, output_schema_version, target)
    return next(
        (registration for registration in _REGISTRATIONS if registration.identity == identity),
        None,
    )
