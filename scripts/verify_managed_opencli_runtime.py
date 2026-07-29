"""Cross-platform acceptance check for the pinned managed OpenCLI runtime."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

OHMYOPENCLI_COMMIT = "b0fdd513f64899b068103ddd7ff0de957d778b5c"
OFFICIAL_SITE_CAPABILITY_COMMIT = "73cc60c83586ef2c95469b3b70d6cfc80fa5bc53"
DOUBAO_CAPABILITY_COMMIT = "b0fdd513f64899b068103ddd7ff0de957d778b5c"
OPENCLI_VERSION = "1.8.5"


class VerificationError(RuntimeError):
    """The installed runtime does not satisfy its audited contract."""


def _json_payload(output: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", output):
        try:
            value, _ = decoder.raw_decode(output[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            return value
    raise VerificationError("managed capability returned no JSON payload")


def verify_runtime(
    *,
    ohmyopencli_root: Path,
    opencli_bin: str = "opencli",
    cdp_endpoint: str | None = None,
    url: str = "https://example.com",
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Verify the fixed checkout, real command, fail-closed route, and optional trace."""

    def checked(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        completed = run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            **kwargs,
        )
        if completed.returncode != 0:
            output = (completed.stdout or "") + (completed.stderr or "")
            raise VerificationError(f"command failed: {' '.join(args)}\n{output}")
        return completed

    root = str(ohmyopencli_root.resolve())
    commit = checked(["git", "-C", root, "rev-parse", "HEAD"]).stdout.strip()
    if commit != OHMYOPENCLI_COMMIT:
        raise VerificationError(f"unexpected OhMyOpenCLI commit: {commit}")
    for source_commit in (
        OFFICIAL_SITE_CAPABILITY_COMMIT,
        DOUBAO_CAPABILITY_COMMIT,
    ):
        checked(
            [
                "git",
                "-C",
                root,
                "merge-base",
                "--is-ancestor",
                source_commit,
                "HEAD",
            ]
        )
    dirty = checked(
        ["git", "-C", root, "status", "--porcelain", "--untracked-files=no"]
    ).stdout.strip()
    if dirty:
        raise VerificationError("OhMyOpenCLI has tracked checkout changes")

    version_output = checked([opencli_bin, "--version"]).stdout
    if OPENCLI_VERSION not in re.findall(r"\d+\.\d+\.\d+", version_output):
        raise VerificationError(f"unexpected OpenCLI version: {version_output.strip()}")
    checked([opencli_bin, "official-site", "observe", "--help"])
    checked([opencli_bin, "doubao", "capture", "--help"])

    dead_env = os.environ.copy()
    dead_env["OPENCLI_CDP_ENDPOINT"] = "http://127.0.0.1:9"
    dead_route_commands = (
        [
            opencli_bin,
            "official-site",
            "observe",
            "--url",
            "https://example.invalid",
            "-f",
            "json",
        ],
        [
            opencli_bin,
            "doubao",
            "capture",
            "managed-runtime-route-probe",
            "-f",
            "json",
        ],
    )
    for dead_command in dead_route_commands:
        dead = run(
            dead_command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=dead_env,
        )
        dead_output = (dead.stdout or "") + (dead.stderr or "")
        if (
            dead.returncode == 0
            or "CDP not reachable at http://127.0.0.1:9" not in dead_output
        ):
            raise VerificationError(
                f"explicit dead CDP route did not fail closed: {' '.join(dead_command)}"
            )

    report: dict[str, Any] = {
        "platform": platform.system(),
        "contract_ready": True,
        "trace_ready": False,
        "runtime": {
            "ohmyopencli_repo_commit": commit,
            "capability_source_commits": {
                "official-site.observe": OFFICIAL_SITE_CAPABILITY_COMMIT,
                "chat-ai.capture:doubao": DOUBAO_CAPABILITY_COMMIT,
            },
            "opencli_version": OPENCLI_VERSION,
        },
        "capability_contracts": {
            "official-site.observe": True,
            "chat-ai.capture:doubao": True,
        },
    }
    if not cdp_endpoint:
        return report

    live_env = os.environ.copy()
    live_env["OPENCLI_CDP_ENDPOINT"] = cdp_endpoint
    live = checked(
        [
            opencli_bin,
            "official-site",
            "observe",
            "--url",
            url,
            "--trace",
            "on",
            "-f",
            "json",
        ],
        env=live_env,
    )
    payload = _json_payload(live.stdout or "")
    expected_identity = ("official-site.observe", "1.0.0", "1")
    actual_identity = (
        payload.get("capabilityId"),
        payload.get("capabilityVersion"),
        payload.get("outputSchemaVersion"),
    )
    if actual_identity != expected_identity:
        raise VerificationError(f"unexpected capability envelope: {actual_identity}")
    trace_match = re.search(
        r"OpenCLI trace artifact:\s*([^\r\n]+)", live.stderr or ""
    )
    if not trace_match:
        raise VerificationError("live capability returned no trace artifact")
    trace_artifact = trace_match.group(1).strip()
    if not Path(trace_artifact).exists():
        raise VerificationError(f"trace artifact does not exist: {trace_artifact}")

    report.update(
        {
            "trace_ready": True,
            "cdp_endpoint": cdp_endpoint,
            "payload": payload,
            "trace_artifact": trace_artifact,
        }
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ohmyopencli-root", type=Path, required=True)
    parser.add_argument("--opencli-bin", default=os.environ.get("OPENCLI_BIN", "opencli"))
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--url", default="https://example.com")
    args = parser.parse_args()
    try:
        report = verify_runtime(
            ohmyopencli_root=args.ohmyopencli_root,
            opencli_bin=args.opencli_bin,
            cdp_endpoint=args.cdp_endpoint,
            url=args.url,
        )
    except VerificationError as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
