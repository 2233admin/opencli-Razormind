from subprocess import CompletedProcess

import pytest

from scripts.verify_managed_opencli_runtime import VerificationError, verify_runtime

PINNED_COMMIT = "bfe1c25b4b12661058dd6e9980c562a09f230cc7"


def _completed(args, returncode=0, stdout="", stderr=""):
    return CompletedProcess(args, returncode, stdout, stderr)


def test_contract_verifier_accepts_the_pinned_real_command(tmp_path):
    calls = []
    responses = iter(
        [
            _completed([], stdout=f"{PINNED_COMMIT}\n"),
            _completed([]),
            _completed([]),
            _completed([]),
            _completed([], stdout="opencli 1.8.5\n"),
            _completed([], stdout="Usage: opencli official-site observe"),
            _completed([], stdout="Usage: opencli doubao capture"),
            _completed(
                [],
                returncode=1,
                stderr="CDP not reachable at http://127.0.0.1:9",
            ),
            _completed(
                [],
                returncode=1,
                stderr="CDP not reachable at http://127.0.0.1:9",
            ),
        ]
    )

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    report = verify_runtime(
        ohmyopencli_root=tmp_path,
        opencli_bin="managed-opencli",
        run=run,
    )

    assert report["contract_ready"] is True
    assert report["trace_ready"] is False
    assert calls[5][0] == [
        "managed-opencli",
        "official-site",
        "observe",
        "--help",
    ]
    assert calls[6][0] == ["managed-opencli", "doubao", "capture", "--help"]
    assert report["capability_contracts"]["chat-ai.capture:doubao"] is True


def test_contract_verifier_rejects_a_missing_doubao_capture_command(tmp_path):
    def run(args, **kwargs):
        if args[-3:] == ["doubao", "capture", "--help"]:
            return _completed(args, returncode=1, stderr="unknown command")
        if args[-2:] == ["rev-parse", "HEAD"]:
            return _completed(args, stdout=f"{PINNED_COMMIT}\n")
        if args[-1:] == ["--version"]:
            return _completed(args, stdout="opencli 1.8.5\n")
        return _completed(args)

    with pytest.raises(VerificationError, match="doubao capture --help"):
        verify_runtime(
            ohmyopencli_root=tmp_path,
            opencli_bin="managed-opencli",
            run=run,
        )


def test_live_verifier_requires_a_real_versioned_payload_and_trace(tmp_path):
    trace_dir = tmp_path / "trace-1"
    trace_dir.mkdir()
    responses = iter(
        [
            _completed([], stdout=f"{PINNED_COMMIT}\n"),
            _completed([]),
            _completed([]),
            _completed([]),
            _completed([], stdout="opencli 1.8.5\n"),
            _completed([], stdout="Usage: opencli official-site observe"),
            _completed([], stdout="Usage: opencli doubao capture"),
            _completed(
                [],
                returncode=1,
                stderr="CDP not reachable at http://127.0.0.1:9",
            ),
            _completed(
                [],
                returncode=1,
                stderr="CDP not reachable at http://127.0.0.1:9",
            ),
            _completed(
                [],
                stdout=(
                    '{"capabilityId":"official-site.observe",'
                    '"capabilityVersion":"1.0.0",'
                    '"outputSchemaVersion":"1","domLength":602900}'
                ),
                stderr=f"OpenCLI trace artifact: {trace_dir}\n",
            ),
        ]
    )

    report = verify_runtime(
        ohmyopencli_root=tmp_path,
        opencli_bin="managed-opencli",
        cdp_endpoint="http://host.docker.internal:9222",
        run=lambda *args, **kwargs: next(responses),
    )

    assert report["contract_ready"] is True
    assert report["trace_ready"] is True
    assert report["payload"]["domLength"] == 602900
    assert report["trace_artifact"] == str(trace_dir)


def test_live_verifier_rejects_success_without_a_trace(tmp_path):
    responses = iter(
        [
            _completed([], stdout=f"{PINNED_COMMIT}\n"),
            _completed([]),
            _completed([]),
            _completed([]),
            _completed([], stdout="opencli 1.8.5\n"),
            _completed([], stdout="Usage: opencli official-site observe"),
            _completed([], stdout="Usage: opencli doubao capture"),
            _completed(
                [],
                returncode=1,
                stderr="CDP not reachable at http://127.0.0.1:9",
            ),
            _completed(
                [],
                returncode=1,
                stderr="CDP not reachable at http://127.0.0.1:9",
            ),
            _completed(
                [],
                stdout=(
                    '{"capabilityId":"official-site.observe",'
                    '"capabilityVersion":"1.0.0",'
                    '"outputSchemaVersion":"1"}'
                ),
            ),
        ]
    )

    with pytest.raises(VerificationError, match="trace artifact"):
        verify_runtime(
            ohmyopencli_root=tmp_path,
            opencli_bin="managed-opencli",
            cdp_endpoint="http://127.0.0.1:9222",
            run=lambda *args, **kwargs: next(responses),
        )
