from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
import yaml
from fastapi.testclient import TestClient
from jose import jwt

ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = ROOT / "tests/acceptance/non_bypass_failure_matrix.py"
spec = importlib.util.spec_from_file_location("non_bypass_failure_matrix", HARNESS_PATH)
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = harness
spec.loader.exec_module(harness)


class ComposeLoader(yaml.SafeLoader):
    pass


def _compose_tag(loader, node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_sequence(node)


ComposeLoader.add_constructor("!override", _compose_tag)
ComposeLoader.add_constructor("!reset", _compose_tag)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _assert_driver_dispatch(capsys, handler: str) -> None:
    result = json.loads(capsys.readouterr().out)
    assert result["handler"] == handler
    assert result["run"] == "r1"
    timing = result["timing"]
    assert timing["startedAt"] <= timing["completedAt"]
    assert timing["completedAt"] - timing["startedAt"] <= timing["deadlineSeconds"] == 360


def public_facts(scenario: str) -> dict:
    collection = {
        "blockingStage": "bridge_unavailable",
        "recoveryAction": "retry",
        "sideEffectUncertainty": True,
    }
    materialization = {
        "status": "unknown",
        "blocker": "none",
        "recoveryAction": "none",
        "manifestHash": None,
        "reconciliationRevision": None,
        "pageSnapshotAsOf": None,
    }
    graph = {
        "pin": None,
        "sequence": None,
        "readBlocker": "none",
        "mutationStatus": "none",
    }
    delivery = {
        "state": "none",
        "outcome": "none",
        "attemptCount": 0,
        "receiptHash": None,
        "reconciliation": "none",
    }
    if scenario == "admin-crash":
        collection = {
            "blockingStage": "none",
            "recoveryAction": "resume",
            "sideEffectUncertainty": True,
        }
    elif scenario == "signed-zero":
        collection = {
            "blockingStage": "none",
            "recoveryAction": "none",
            "sideEffectUncertainty": False,
        }
        materialization["status"] = "completed_empty"
    elif scenario in {"no-report", "crash-after-ingest"}:
        collection = {
            "blockingStage": "callback_missing",
            "recoveryAction": "recover",
            "sideEffectUncertainty": True,
        }
        materialization.update(
            status="indeterminate",
            blocker="missing_report",
            recoveryAction="recover",
        )
    elif scenario == "duplicate-dlq":
        collection = {
            "blockingStage": "duplicate",
            "recoveryAction": "recover",
            "sideEffectUncertainty": False,
        }
        materialization.update(
            status="completed",
            blocker="retained_dlq",
            recoveryAction="recover",
            manifestHash=_hash("manifest"),
            reconciliationRevision=1,
        )
    elif scenario == "query-page-race":
        collection = {
            "blockingStage": "none",
            "recoveryAction": "recover",
            "sideEffectUncertainty": False,
        }
        materialization.update(
            status="completed",
            recoveryAction="recover",
            manifestHash=_hash("manifest"),
            reconciliationRevision=1,
            pageSnapshotAsOf="2026-08-30T00:00:00Z",
        )
    elif scenario == "graph-stale-auth-cas-retract":
        collection = {
            "blockingStage": "none",
            "recoveryAction": "recover",
            "sideEffectUncertainty": False,
        }
        materialization.update(
            status="completed",
            recoveryAction="recover",
            manifestHash=_hash("manifest"),
            reconciliationRevision=1,
        )
        graph = {
            "pin": _hash("retracted-pin"),
            "sequence": 7,
            "readBlocker": "retract",
            "mutationStatus": "re_review_required",
        }
    elif scenario == "amendment-decision-conflict":
        collection = {
            "blockingStage": "none",
            "recoveryAction": "recover",
            "sideEffectUncertainty": False,
        }
        materialization.update(
            status="completed",
            recoveryAction="recover",
            manifestHash=_hash("amendment"),
            reconciliationRevision=2,
        )
        graph = {
            "pin": _hash("new-pin"),
            "sequence": 8,
            "readBlocker": "stale_manifest",
            "mutationStatus": "re_review_required",
        }
    elif scenario == "receiver-recovery":
        collection = {
            "blockingStage": "none",
            "recoveryAction": "recover",
            "sideEffectUncertainty": False,
        }
        materialization.update(
            status="completed",
            recoveryAction="recover",
            manifestHash=_hash("receiver"),
            reconciliationRevision=1,
        )
        graph = {
            "pin": _hash("receiver-pin"),
            "sequence": 3,
            "readBlocker": "none",
            "mutationStatus": "none",
        }
        delivery = {
            "state": "settled",
            "outcome": "accepted",
            "attemptCount": 3,
            "receiptHash": _hash("receiver-receipt"),
            "reconciliation": "signed_accepted",
        }
    elif scenario == "cancel-before-dispatch":
        delivery = {
            "state": "cancelled",
            "outcome": "none",
            "attemptCount": 0,
            "receiptHash": None,
            "reconciliation": "none",
        }
    elif scenario == "cancel-in-flight":
        delivery = {
            "state": "unknown",
            "outcome": "unknown",
            "attemptCount": 1,
            "receiptHash": None,
            "reconciliation": "unknown",
        }
    return {
        "scenario": scenario,
        "run": f"run-{scenario}",
        "fault": scenario,
        "actuator": {
            "name": (
                "proof-iii-actuator"
                if scenario in {"query-page-race", "amendment-decision-conflict"}
                else "proof-public-driver"
            ),
            "invocationHash": _hash(f"actor-{scenario}"),
        },
        "correlation": {
            "commandId": "command",
            "attemptId": "attempt",
            "workflowRunId": "workflow",
            "hashes": {"public": _hash(scenario)},
        },
        "collection": collection,
        "materialization": materialization,
        "graph": graph,
        "delivery": delivery,
        "redactionProfile": "failure-v1",
        "timing": {
            "startedAt": 1,
            "completedAt": 2,
            "deadlineSeconds": 360,
        },
        "governanceReference": {
            "artifactId": f"artifact-{scenario}",
            "keyId": "key-1",
            "trustRootFingerprint": _hash("trust"),
        },
        "authority": "authenticated-scoped-public-api",
    }


@pytest.mark.parametrize("scenario", sorted(harness.SCENARIOS))
def test_every_matrix_row_normalizes_only_authenticated_public_facts(scenario: str):
    result = harness.normalize_public_facts(public_facts(scenario))
    assert result["schemaVersion"] == "ScenarioResultV1"
    assert result["forbiddenFacts"] == {
        "adminCreatedFallback": False,
        "lateEffectAbsenceClaim": False,
        "containerAuthority": False,
        "pageFinality": False,
    }


@pytest.mark.parametrize(
    ("scenario", "wrong_name"),
    [
        ("admin-crash", "proof-iii-actuator"),
        ("query-page-race", "proof-public-driver"),
    ],
)
def test_mismatched_scenario_actuator_name_is_rejected(scenario: str, wrong_name: str):
    facts = public_facts(scenario)
    facts["actuator"]["name"] = wrong_name
    with pytest.raises(RuntimeError, match="scenario actuator"):
        harness.normalize_public_facts(facts)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario", "unknown-scenario"),
        ("actuator.name", None),
        ("delivery.attemptCount", True),
        ("timing.startedAt", True),
    ],
)
def test_contract_rejects_unknown_and_non_exact_public_values(field: str, value: object):
    facts = public_facts("signed-zero")
    target = facts
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    with pytest.raises(RuntimeError):
        harness.normalize_public_facts(facts)


def test_timing_beyond_dispatch_deadline_is_rejected():
    facts = public_facts("signed-zero")
    facts["timing"]["completedAt"] = facts["timing"]["startedAt"] + 361
    with pytest.raises(RuntimeError, match="timing"):
        harness.normalize_public_facts(facts)


def test_internal_control_state_and_terminal_page_inference_are_rejected():
    facts = public_facts("query-page-race")
    facts["gateState"] = "released"
    rejection = (
        harness.PublicFactRejectedError
        if hasattr(harness, "PublicFactRejectedError")
        else harness.PublicFactRejected
    )
    with pytest.raises(rejection):
        harness.normalize_public_facts(facts)


def test_fixture_is_pinned_and_has_only_one_zero_and_hundred_operations():
    fixture = ROOT / "tests/acceptance/fixtures/opencli-failure-proof"
    recorded = (
        (ROOT / "tests/acceptance/fixtures/opencli-failure-proof.sha256").read_text().split()[0]
    )
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == recorded
    assert (
        "hundred" in fixture.read_text()
        and "zero" in fixture.read_text()
        and "one" in fixture.read_text()
    )


def test_overlay_has_no_host_ports_and_internal_fault_network():
    compose = yaml.load(
        (ROOT / "docker-compose.non-bypass-failure.yml").read_text(),
        Loader=ComposeLoader,
    )
    assert compose["networks"]["proof-fault"]["internal"] is True
    assert all("ports" not in service for service in compose["services"].values())
    assert {
        "proof-iii-actuator",
        "proof-odp-query-pg-gate",
        "proof-governance",
        "proof-admin-control",
        "proof-odp-ingest-redis-mutator",
    } <= set(compose["services"])
    assert "proof-fault-gateway" not in compose["services"]
    assert not (ROOT / "tests/acceptance/fault_tools/semantic_gateway.py").exists()
    assert compose["services"]["proof-odp-ingest"]["environment"]["ODP_REDIS_URL"].startswith(
        "redis://proof-odp-ingest-redis-mutator:"
    )
    assert (
        compose["services"]["proof-odp-ingest-redis-mutator"]["environment"]["GATEWAY_MODE"]
        == "ingest-redis-payload-mutator"
    )

    governance = compose["services"]["proof-governance"]
    assert governance["networks"] == ["proof-control"]
    assert governance["depends_on"]["proof-oidc"]["condition"] == "service_healthy"
    assert governance["environment"]["PROOF_GOVERNANCE_ROOT"] == "/proof-artifacts/governance"
    assert governance["environment"]["PROOF_GOVERNANCE_KEY_ROOT"] == "/proof-governance-keys"
    assert governance["volumes"] == [
        "${PROOF_ARTIFACT_DIR:-./.artifacts/non-bypass-failures}:/proof-artifacts",
        "proof-governance-keys:/proof-governance-keys",
    ]
    assert compose["volumes"]["proof-governance-keys"]["labels"] == {
        "com.opencli.proof.release": "failure-recovery",
        "com.opencli.proof.scenario": "${PROOF_SCENARIO:-unbound}",
    }
    assert [
        name
        for name, service in compose["services"].items()
        if any("proof-governance-keys" in mount for mount in service.get("volumes", []))
    ] == ["proof-governance-key-init", "proof-governance"]
    assert governance["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "-fsS",
        "http://localhost:8000/health",
    ]
    driver = compose["services"]["proof-driver"]
    assert driver["depends_on"]["proof-governance"]["condition"] == "service_healthy"
    assert governance["depends_on"]["proof-governance-key-init"] == {
        "condition": "service_completed_successfully"
    }
    assert compose["services"]["proof-governance-key-init"]["network_mode"] == "none"
    for service, port in {
        "proof-iii-actuator": 8000,
        "proof-odp-http-gateway": 8040,
        "proof-odp-ingest-redis-gateway": 8081,
        "proof-odp-ingest-redis-mutator": 8084,
        "proof-odp-store-pg-gateway": 8082,
        "proof-odp-store-redis-gateway": 8083,
    }.items():
        assert compose["services"][service]["healthcheck"]["test"] == [
            "CMD",
            "curl",
            "-fsS",
            f"http://localhost:{port}/health",
        ]
        assert driver["depends_on"][service]["condition"] == "service_healthy"
    assert "proof-delivery-proxy" not in driver["depends_on"]
    assert set(driver["environment"]) >= {
        "PROOF_BUNDLE_WRITER_JWT",
        "PROOF_KEY_ADMIN_JWT",
    }


def test_failure_runner_has_no_local_governance_authority():
    source = (ROOT / "scripts/run_non_bypass_failure_matrix.py").read_text()
    assert "ProofBundleStore" not in source
    assert "Principal" not in source
    assert "Ed25519PrivateKey" not in source
    assert "http://proof-governance:8000/v1" in source

    assert "report-diagnostics" not in source
    assert "PROOF_HASH_KEY" not in source


def test_governance_identities_are_opt_in_and_share_the_public_jwks(tmp_path):
    runner = _runner_module()
    ledger = runner.HappyRunLedger("run", "project", tmp_path / "scratch", tmp_path)
    ledger.scratch.mkdir()
    default: dict[str, str] = {}
    runner._make_identities(ledger, default)
    assert "PROOF_BUNDLE_WRITER_JWT" not in default
    assert "PROOF_KEY_ADMIN_JWT" not in default

    scope = {
        "workspace": "failure",
        "project": "project",
        "workflow": "failure-matrix",
        "run": "run",
    }
    governed: dict[str, str] = {}
    runner._make_identities(ledger, governed, governance_scope=scope)
    jwks = json.loads((ledger.scratch / "jwks.json").read_text())
    assert jwks["keys"][0]["kid"] == "proof-jwks-v1"
    for environment, role in (
        ("PROOF_BUNDLE_WRITER_JWT", "bundle-writer"),
        ("PROOF_KEY_ADMIN_JWT", "key-admin"),
    ):
        claims = jwt.get_unverified_claims(governed[environment])
        assert jwt.get_unverified_header(governed[environment])["kid"] == jwks["keys"][0]["kid"]
        assert claims["aud"] == "proof-governance"
        assert claims["role"] == role
        assert claims["proof_scope"] == scope
        assert claims["sub"] == f"proof-{role}"


def test_callback_relay_routes_only_the_three_real_callback_paths(monkeypatch):
    relay_path = ROOT / "tests/acceptance/fault_tools/callback_relay.py"
    relay_spec = importlib.util.spec_from_file_location("callback_relay", relay_path)
    assert relay_spec and relay_spec.loader
    relay = importlib.util.module_from_spec(relay_spec)
    sys.modules[relay_spec.name] = relay
    relay_spec.loader.exec_module(relay)
    calls: list[str] = []

    def proxied(url: str, **kwargs):
        calls.append(url)
        import httpx

        assert kwargs["headers"] == {
            "authorization": "Bearer collector-fleet-token",
            "x-iii-bridge-token": "collector-bridge-token",
            "content-type": "application/json",
        }
        return httpx.Response(
            202,
            content=b'{"data":{}}',
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(relay.httpx, "post", proxied)
    client = TestClient(relay.app)
    response = client.post(
        "/api/v1/iii-collections/lifecycle",
        content=b"{}",
        headers={
            "authorization": "Bearer collector-fleet-token",
            "x-iii-bridge-token": "collector-bridge-token",
        },
    )
    assert response.status_code == 202
    assert "collector-fleet-token" not in response.text
    assert "collector-bridge-token" not in response.text
    assert calls == ["http://proof-admin:8000/api/v1/iii-collections/lifecycle"]
    assert client.post("/not-an-allowlisted-callback", content=b"{}").status_code == 404


def test_receipt_gate_counts_the_real_held_callback(monkeypatch):
    relay_path = ROOT / "tests/acceptance/fault_tools/callback_relay.py"
    relay_spec = importlib.util.spec_from_file_location("callback_relay_counter", relay_path)
    assert relay_spec and relay_spec.loader
    relay = importlib.util.module_from_spec(relay_spec)
    sys.modules[relay_spec.name] = relay
    relay_spec.loader.exec_module(relay)
    monkeypatch.setenv("API_AUTH_TOKEN", "gate-token")
    monkeypatch.setattr(
        relay.httpx,
        "post",
        lambda *_args, **_kwargs: __import__("httpx").Response(
            202, content=b'{"data":{}}', headers={"content-type": "application/json"}
        ),
    )
    with TestClient(relay.app) as client:
        headers = {"X-API-Token": "gate-token"}
        assert (
            client.post("/_gate/receipt", json={"mode": "hold"}, headers=headers).status_code == 200
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            callback = executor.submit(
                client.post,
                "/api/v1/iii-collections/ingress-receipts",
                content=b"{}",
            )
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                held = client.get("/_gate/receipt-held", headers=headers).json()
                if held["count"] == 1:
                    break
            assert held == {"count": 1}
            assert (
                client.post("/_gate/receipt", json={"mode": "forward"}, headers=headers).status_code
                == 200
            )
            assert callback.result(timeout=2).status_code == 202


@pytest.mark.parametrize("scenario", ["admin-crash", "no-report"])
def test_failure_driver_main_prints_its_fact_document(monkeypatch, capsys, scenario):
    driver_path = ROOT / "tests/acceptance/non_bypass_failure_driver.py"
    driver_spec = importlib.util.spec_from_file_location(
        f"failure_driver_main_{scenario}", driver_path
    )
    assert driver_spec and driver_spec.loader
    driver = importlib.util.module_from_spec(driver_spec)
    sys.modules[driver_spec.name] = driver
    driver_spec.loader.exec_module(driver)
    clock = iter((100, 103))
    monkeypatch.setattr(driver.time, "time", lambda: next(clock))
    monkeypatch.setattr(
        driver,
        "admin_crash",
        lambda run, scenario: {"run": run, "scenario": scenario},
    )
    monkeypatch.setattr(sys, "argv", ["driver", "--scenario", scenario, "--run", "r1"])
    assert driver.main() == 0
    assert __import__("json").loads(capsys.readouterr().out) == {
        "run": "r1",
        "scenario": scenario,
        "timing": {"startedAt": 100, "completedAt": 103, "deadlineSeconds": 360},
    }


def test_failure_runner_writes_selected_release_before_waiting():
    source = (ROOT / "scripts/run_non_bypass_failure_matrix.py").read_text(encoding="utf-8")
    gate_section = source[source.index('signal_name = "iii-ready"') :]
    release = 'f"{ledger.project}.{release_name}").write_text'
    assert gate_section.index(release) < gate_section.index("process.communicate(timeout=120)")


def test_published_run_retries_only_the_documented_visibility_409(monkeypatch):
    driver_path = ROOT / "tests/acceptance/non_bypass_failure_driver.py"
    driver_spec = importlib.util.spec_from_file_location("failure_driver_retry", driver_path)
    assert driver_spec and driver_spec.loader
    driver = importlib.util.module_from_spec(driver_spec)
    sys.modules[driver_spec.name] = driver
    driver_spec.loader.exec_module(driver)
    import httpx

    responses = iter(
        [
            httpx.Response(409, text="Workflow must be published before API execution"),
            httpx.Response(200, json={"data": {"runId": "run-1"}}),
        ]
    )

    class Client:
        def post(self, *_args, **_kwargs):
            return next(responses)

    monkeypatch.setattr(driver.time, "monotonic", lambda: 0)
    monkeypatch.setattr(driver.time, "sleep", lambda _seconds: None)
    assert driver._post_published_run(
        Client(),
        "http://api",
        "/run",
        {"idempotencyKey": "same"},
        {},
    ) == {"runId": "run-1"}


def test_crash_after_ingest_uses_isolated_two_phase_orchestration():
    driver = (ROOT / "tests/acceptance/non_bypass_failure_driver.py").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/run_non_bypass_failure_matrix.py").read_text(encoding="utf-8")
    assert "def crash_after_ingest(" in driver
    assert all(
        name in driver for name in ("arm-report-hold", "ingress-observed", "collector-stopped")
    )
    assert "def _facts_from_crash_after_ingest(" in runner
    assert '"stop", "proof-collector"' in runner
    assert '{"mode":"hold"}' in runner


@pytest.mark.parametrize(
    ("scenario", "handler"),
    [
        ("admin-crash", "admin_crash"),
        ("no-report", "admin_crash"),
        ("signed-zero", "admin_crash"),
        ("iii-unreachable", "iii_unreachable"),
        ("crash-after-ingest", "crash_after_ingest"),
        ("ingest-redis-store-loss", "ingest_redis_store_loss"),
    ],
)
def test_failure_driver_dispatches_every_implemented_scenario(
    monkeypatch,
    capsys,
    scenario,
    handler,
):
    driver_path = ROOT / "tests/acceptance/non_bypass_failure_driver.py"
    driver_spec = importlib.util.spec_from_file_location(f"failure_driver_{scenario}", driver_path)
    assert driver_spec and driver_spec.loader
    driver = importlib.util.module_from_spec(driver_spec)
    sys.modules[driver_spec.name] = driver
    driver_spec.loader.exec_module(driver)
    monkeypatch.setattr(
        driver,
        "admin_crash",
        lambda run, name: {"handler": "admin_crash", "scenario": name},
    )
    monkeypatch.setattr(
        driver,
        "iii_unreachable",
        lambda run: {
            "handler": "iii_unreachable",
            "scenario": "iii-unreachable",
        },
    )
    monkeypatch.setattr(
        driver,
        "ingest_redis_store_loss",
        lambda run: {
            "handler": "ingest_redis_store_loss",
            "scenario": "ingest-redis-store-loss",
        },
    )
    monkeypatch.setattr(
        driver,
        "crash_after_ingest",
        lambda run: {
            "handler": "crash_after_ingest",
            "scenario": "crash-after-ingest",
        },
    )
    monkeypatch.setattr(sys, "argv", ["driver", "--scenario", scenario, "--run", "r1"])
    assert driver.main() == 0
    assert __import__("json").loads(capsys.readouterr().out)["handler"] == handler


def test_failure_overlay_routes_all_odp_writers_through_named_fault_gateways():
    compose = yaml.load(
        (ROOT / "docker-compose.non-bypass-failure.yml").read_text(),
        Loader=ComposeLoader,
    )
    services = compose["services"]
    assert {
        "proof-odp-http-gateway",
        "proof-odp-ingest-redis-gateway",
        "proof-odp-ingest-redis-mutator",
        "proof-odp-store-pg-gateway",
        "proof-odp-store-redis-gateway",
    } <= set(services)
    assert (
        services["proof-collector"]["environment"]["ODP_INGEST_URL"]
        == "http://proof-odp-http-gateway:8040"
    )
    assert (
        services["proof-bridge"]["environment"]["ODP_INGEST_URL"]
        == "http://proof-odp-http-gateway:8040"
    )
    assert (
        services["proof-odp-ingest"]["environment"]["ODP_REDIS_URL"]
        == "redis://proof-odp-ingest-redis-mutator:6379/2"
    )
    assert (
        services["proof-odp-store"]["environment"]["ODP_REDIS_URL"]
        == "redis://proof-odp-store-redis-gateway:6379/2"
    )
    assert (
        "@proof-odp-store-pg-gateway:5432/"
        in services["proof-odp-store"]["environment"]["ODP_DATABASE_URL"]
    )


def test_storage_loss_row_uses_public_helpers_and_single_scenario_runner_selection():
    driver = (ROOT / "tests/acceptance/non_bypass_failure_driver.py").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/run_non_bypass_failure_matrix.py").read_text(encoding="utf-8")
    assert all(
        f"def {name}(" in driver
        for name in (
            "public_setup",
            "public_submit",
            "public_status",
            "public_materialize",
            "public_recover",
            "ingest_redis_store_loss",
        )
    )
    assert "store-redis-committed-xadd" in driver and "store-commit-ready" in driver
    assert 'parser.add_argument("--scenario", choices=SCENARIO_ORDER)' in runner
    assert "selected = (scenario,) if scenario else SCENARIO_ORDER" in runner


def test_duplicate_dlq_row_binds_replay_duplicate_and_retention_public_outcomes():
    driver = (ROOT / "tests/acceptance/non_bypass_failure_driver.py").read_text(encoding="utf-8")
    assert all(
        f"def {name}(" in driver
        for name in (
            "duplicate_dlq",
            "_wait_for_ingress_receipt",
            "_wait_for_materialization",
            "public_disposable_run",
        )
    )
    assert "ingest-redis-payload-mutator" in driver
    assert '"duplicate-dlq"' in driver
    assert all(
        name in driver
        for name in (
            "replay_same_intent",
            "duplicate_signed_receipt",
            "retained_dlq_materialization",
            "unknown_retention_materialization",
        )
    )
    runner = (ROOT / "scripts/run_non_bypass_failure_matrix.py").read_text(encoding="utf-8")
    assert '"duplicate-dlq"' in runner


def test_query_page_race_uses_real_iii_and_scoped_public_reconciliation_only():
    driver = (ROOT / "tests/acceptance/non_bypass_failure_driver.py").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/run_non_bypass_failure_matrix.py").read_text(encoding="utf-8")
    actuator = (ROOT / "tests/acceptance/fault_tools/proof_iii_actuator.py").read_text(
        encoding="utf-8"
    )
    compose = yaml.load(
        (ROOT / "docker-compose.non-bypass-failure.yml").read_text(),
        Loader=ComposeLoader,
    )

    assert all(
        name in driver
        for name in (
            "def query_page_race(",
            "_wait_for_expected_key_report",
            "_wait_for_held_receipts",
            "_actuate_correlated_ingress",
            "_wait_for_query_page_gate",
            "collection_before_actor",
            "original_ingress_receipt",
            "materialization_after_recover",
            "record_present",
        )
    )
    assert '"query-page-race"' in runner
    assert "communicate(timeout=360)" in runner
    assert "odp.ingest::batch" in actuator
    assert "admin_collection" in actuator
    assert "actor credential denied" in actuator
    assert "PROOF_III_BRIDGE_URL" not in actuator
    assert compose["services"]["proof-iii-actuator"]["environment"]["PROOF_III_URL"] == (
        "ws://proof-iii:49134"
    )
    assert compose["services"]["proof-odp-query"]["environment"][
        "ODP_QUERY_DATABASE_URL"
    ].startswith("postgresql://proof:proof@proof-odp-query-pg-gate:")


def test_failure_driver_dispatches_query_page_race(monkeypatch, capsys):
    driver = _failure_driver_module()
    monkeypatch.setattr(
        driver, "query_page_race", lambda run: {"handler": "query_page_race", "run": run}
    )
    monkeypatch.setattr(sys, "argv", ["driver", "--scenario", "query-page-race", "--run", "r1"])

    assert driver.main() == 0
    _assert_driver_dispatch(capsys, "query_page_race")


def test_graph_stale_auth_cas_retract_uses_public_graph_boundaries():
    driver = (ROOT / "tests/acceptance/non_bypass_failure_driver.py").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/run_non_bypass_failure_matrix.py").read_text(encoding="utf-8")

    assert all(
        name in driver
        for name in (
            "def graph_stale_auth_cas_retract(",
            "proof-reviewer",
            "wrong-capability verification",
            "stale reviewer retraction",
            "pinned_reference_mismatch",
            "reviewer legal retraction",
            "graph_final_authenticated_read_two",
            '"readBlocker": "retract"',
            '"mutationStatus": "re_review_required"',
        )
    )
    assert "AsyncSessionLocal" not in driver
    assert "StudioWorkspace" not in driver
    assert '"graph-stale-auth-cas-retract"' in runner
    assert "communicate(timeout=360)" in runner


def test_failure_driver_dispatches_graph_stale_auth_cas_retract(monkeypatch, capsys):
    driver = _failure_driver_module()
    monkeypatch.setattr(
        driver,
        "graph_stale_auth_cas_retract",
        lambda run: {"handler": "graph-stale-auth-cas-retract", "run": run},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["driver", "--scenario", "graph-stale-auth-cas-retract", "--run", "r1"],
    )

    assert driver.main() == 0
    _assert_driver_dispatch(capsys, "graph-stale-auth-cas-retract")


def test_amendment_decision_conflict_uses_public_duplicate_recovery_and_bindings():
    driver = (ROOT / "tests/acceptance/non_bypass_failure_driver.py").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/run_non_bypass_failure_matrix.py").read_text(encoding="utf-8")

    assert all(
        name in driver
        for name in (
            "def amendment_decision_conflict(",
            "_fixture_one_event_id",
            "amendment_duplicate",
            "signed_duplicate_receipt",
            "materialization_n_plus_one",
            "manifest_superseded",
            "supersedesEventId",
            "old_blocked_pin_conflict",
            "changed_decision_replay_conflict",
            '"readBlocker": "stale_manifest"',
            '"mutationStatus": "re_review_required"',
        )
    )
    assert "AsyncSessionLocal" not in driver
    assert "StudioWorkspace" not in driver
    assert '"amendment-decision-conflict"' in runner
    assert "communicate(timeout=360)" in runner


def test_failure_driver_dispatches_amendment_decision_conflict(monkeypatch, capsys):
    driver = _failure_driver_module()
    monkeypatch.setattr(
        driver,
        "amendment_decision_conflict",
        lambda run: {"handler": "amendment-decision-conflict", "run": run},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["driver", "--scenario", "amendment-decision-conflict", "--run", "r1"],
    )

    assert driver.main() == 0
    _assert_driver_dispatch(capsys, "amendment-decision-conflict")


def test_receiver_recovery_driver_uses_public_attempts_and_signed_reconciliation():
    driver = (ROOT / "tests/acceptance/non_bypass_failure_driver.py").read_text(encoding="utf-8")

    assert all(
        value in driver
        for value in (
            "def receiver_recovery(",
            "corrupt_mac",
            "execution_deadline = time.monotonic() + 110",
            "restart_reconciliation_deadline = time.monotonic() + 60",
            "receiver-restart-ready",
            "/reconcile",
            '"state": "settled"',
            '"attemptCount": 3',
            "signed_{outcome}",
        )
    )
    assert "receiver delivery and reconciliation exceeded 110 seconds" not in driver
    assert "AsyncSessionLocal" not in driver
    assert "StudioWorkspace" not in driver
    assert "proof-controlled-receiver:8000" not in driver


def test_receiver_deadlines_are_phase_local_and_deterministic():
    driver = _failure_driver_module()
    driver._require_within_deadline(110, "delivery", clock=lambda: 110)
    driver._require_within_deadline(60, "restart and reconciliation", clock=lambda: 60)
    with pytest.raises(RuntimeError, match="delivery exceeded"):
        driver._require_within_deadline(110, "delivery", clock=lambda: 110.001)
    with pytest.raises(RuntimeError, match="restart and reconciliation exceeded"):
        driver._require_within_deadline(60, "restart and reconciliation", clock=lambda: 60.001)


def test_receiver_recovery_overlay_isolates_real_receiver_behind_tls_proxy():
    base = yaml.load(
        (ROOT / "docker-compose.non-bypass-acceptance.yml").read_text(encoding="utf-8"),
        Loader=ComposeLoader,
    )
    overlay = yaml.load(
        (ROOT / "docker-compose.non-bypass-failure.yml").read_text(encoding="utf-8"),
        Loader=ComposeLoader,
    )
    services = overlay["services"]
    proxy = services["proof-delivery-proxy"]

    assert proxy["networks"]["proof-failure-receiver"]["ipv4_address"] == (
        "${PROOF_RECEIVER_IP:?required}"
    )
    receiver_network = overlay["networks"]["proof-failure-receiver"]["ipam"]["config"][0]
    assert receiver_network == {
        "subnet": "${PROOF_RECEIVER_SUBNET:?required}",
        "gateway": "${PROOF_RECEIVER_GATEWAY:?required}",
    }
    assert set(proxy["networks"]) == {
        "proof-failure-receiver",
        "proof-receiver-backend",
        "proof-fault",
    }
    assert services["proof-controlled-receiver"]["networks"] == [
        "proof-receiver-backend",
        "proof-receiver-db",
    ]
    assert services["proof-receiver-postgres"]["networks"] == ["proof-receiver-db"]
    assert "proof-receiver-backend" not in services["proof-admin"]["networks"]
    assert "proof-receiver-db" not in services["proof-admin-control"]["networks"]
    assert base["services"]["proof-controlled-receiver"]["networks"] == {
        "proof-receiver": {"ipv4_address": "8.8.8.8"}
    }


def test_receiver_profile_routes_only_receiver_dependent_scenarios():
    runner = _runner_module()
    overlay = yaml.load(
        (ROOT / "docker-compose.non-bypass-failure.yml").read_text(encoding="utf-8"),
        Loader=ComposeLoader,
    )

    assert {
        service: overlay["services"][service]["profiles"]
        for service in (
            "proof-delivery-proxy",
            "proof-controlled-receiver",
            "proof-receiver-postgres",
        )
    } == {
        "proof-delivery-proxy": ["receiver"],
        "proof-controlled-receiver": ["receiver"],
        "proof-receiver-postgres": ["receiver"],
    }
    assert runner._scenario_compose_profiles("receiver-recovery") == "receiver"
    assert runner._scenario_compose_profiles("cancel-in-flight") == "receiver"
    assert runner._scenario_compose_profiles("cancel-before-dispatch") == ""


def test_driver_waits_for_healthy_control_admin_before_public_boundary():
    overlay = yaml.load(
        (ROOT / "docker-compose.non-bypass-failure.yml").read_text(encoding="utf-8"),
        Loader=ComposeLoader,
    )
    services = overlay["services"]

    assert services["proof-driver"]["depends_on"]["proof-admin-control"] == {
        "condition": "service_healthy"
    }
    assert services["proof-admin-control"]["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "-fsS",
        "http://localhost:8000/health",
    ]


def test_receiver_address_tuple_is_deterministic_global_and_overrideable():
    runner = _runner_module()

    first = runner._receiver_address_values("nbf-cancel-before-dispatch-a1b2c3")
    again = runner._receiver_address_values("nbf-cancel-before-dispatch-a1b2c3")
    other = runner._receiver_address_values("nbf-cancel-before-dispatch-d4e5f6")

    assert first == again
    assert first != other
    assert ipaddress.ip_address(first["PROOF_RECEIVER_IP"]).is_global
    assert ipaddress.ip_address(first["PROOF_RECEIVER_IP"]) in ipaddress.ip_network(
        first["PROOF_RECEIVER_SUBNET"]
    )
    assert first["PROOF_RECEIVER_IP"] != first["PROOF_RECEIVER_GATEWAY"]
    receiver_network = ipaddress.ip_network(first["PROOF_RECEIVER_SUBNET"])
    assert ipaddress.ip_address(first["PROOF_RECEIVER_IP"]) == (
        receiver_network.network_address + 2
    )
    override = {
        "PROOF_RECEIVER_IP": "12.34.56.2",
        "PROOF_RECEIVER_SUBNET": "12.34.56.0/24",
        "PROOF_RECEIVER_GATEWAY": "12.34.56.254",
    }
    assert runner._receiver_address_values("any-project", override) == override
    for invalid in (
        {
            "PROOF_RECEIVER_IP": "10.0.0.1",
            "PROOF_RECEIVER_SUBNET": "10.0.0.0/24",
            "PROOF_RECEIVER_GATEWAY": "10.0.0.254",
        },
        {
            "PROOF_RECEIVER_IP": "11.1.2.1",
            "PROOF_RECEIVER_SUBNET": "11.1.1.0/24",
            "PROOF_RECEIVER_GATEWAY": "11.1.1.254",
        },
        {
            "PROOF_RECEIVER_IP": "11.1.1.1",
            "PROOF_RECEIVER_SUBNET": "11.1.1.0/24",
            "PROOF_RECEIVER_GATEWAY": "11.1.1.1",
        },
        {
            "PROOF_RECEIVER_IP": "12.34.56.1",
            "PROOF_RECEIVER_SUBNET": "12.34.56.0/24",
            "PROOF_RECEIVER_GATEWAY": "12.34.56.254",
        },
    ):
        with pytest.raises(runner.FailureRunRejectedError):
            runner._receiver_address_values("invalid-project", invalid)


def test_cancel_before_uses_no_receiver_profile_but_configures_authorization_tuple(
    monkeypatch, tmp_path
):
    runner = _runner_module()
    assert runner._scenario_compose_profiles("cancel-before-dispatch") == ""
    ledger = runner.ScenarioLedger("cancel-before-dispatch", "nbf-cancel-a1b2", tmp_path, tmp_path)
    values = {
        **runner._receiver_address_values(ledger.project),
        "CONTROLLED_RECEIVER_CREDENTIALS_JSON": json.dumps({"credential": "secret"}),
        "CONTROLLED_RECEIVER_INBOUND_KEYS_JSON": json.dumps({"request-key": "public"}),
        "CONTROLLED_RECEIVER_RECEIPT_KEYS_JSON": json.dumps({"receipt-key": "public"}),
    }
    monkeypatch.setattr(runner, "_run", lambda *_args, **_kwargs: "")

    runner._configure_failure_receiver(ledger, values)

    registry = json.loads(values["CONTROLLED_RECEIVER_REGISTRY_JSON"])["receiver-channel-proof"]
    assert registry["url"].startswith(f"https://{values['PROOF_RECEIVER_IP']}:")
    assert registry["allowedNetworks"] == [values["PROOF_RECEIVER_SUBNET"]]
    assert f"IP:{values['PROOF_RECEIVER_IP']}" in (tmp_path / "receiver.ext").read_text()
    overlay = yaml.load(
        (ROOT / "docker-compose.non-bypass-failure.yml").read_text(encoding="utf-8"),
        Loader=ComposeLoader,
    )
    receiver_network = overlay["networks"]["proof-failure-receiver"]["ipam"]["config"][0]
    compose_tuple = {
        "PROOF_RECEIVER_IP": overlay["services"]["proof-delivery-proxy"]["networks"][
            "proof-failure-receiver"
        ]["ipv4_address"],
        "PROOF_RECEIVER_SUBNET": receiver_network["subnet"],
        "PROOF_RECEIVER_GATEWAY": receiver_network["gateway"],
    }
    assert compose_tuple == {
        name: f"${{{name}:?required}}" for name in values if name.startswith("PROOF_RECEIVER_")
    }


def test_catalog_digest_is_address_independent_but_catalog_config_has_a_tuple(
    monkeypatch,
):
    runner = _runner_module()
    configured_envs: list[dict[str, str]] = []
    monkeypatch.setattr(runner, "_catalog_digest", lambda *_args: "a" * 64)
    monkeypatch.setattr(runner, "_catalog_build_commands", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        runner,
        "_compose",
        lambda _ledger, env, *_args, **_kwargs: (
            configured_envs.append(env)
            or "\n".join(["pinned", *runner._catalog_images("a" * 64).values()])
        ),
    )
    monkeypatch.setattr(
        runner,
        "_inspect_images",
        lambda *_args, **_kwargs: {"pinned": "sha256:pinned"},
    )
    monkeypatch.setattr(runner, "_fixture_digest", lambda: "fixture")
    monkeypatch.setattr(runner, "PINNED_III", "pinned")

    catalog = runner._build_catalog(
        {},
        ROOT / "docker-compose.non-bypass-acceptance.yml",
        ROOT / "docker-compose.non-bypass-failure.yml",
    )

    assert catalog["digest"] == "a" * 64
    catalog_env = configured_envs[0]
    receiver_values = runner._receiver_address_values("nbf-catalog-" + "a" * 12, {})
    assert {name: catalog_env[name] for name in receiver_values} == receiver_values
    assert catalog_env["PROOF_CATALOG_DIGEST"] == "a" * 64

    assert catalog_env["COMPOSE_PROFILES"] == "receiver"


def test_receiver_recovery_runner_restarts_only_delivery_boundary():
    runner = (ROOT / "scripts/run_non_bypass_failure_matrix.py").read_text(encoding="utf-8")

    assert '"receiver-recovery"' in runner
    assert "def _facts_from_receiver_recovery(" in runner
    recovery = runner.split("def _facts_from_receiver_recovery(", 1)[1].split(
        "def _facts_from_driver(", 1
    )[0]
    assert '"restart"' in recovery
    assert '"proof-delivery-proxy"' in recovery
    assert '"proof-controlled-receiver"' in recovery
    assert "proof-receiver-postgres" not in recovery


def test_failure_driver_dispatches_receiver_recovery(monkeypatch, capsys):
    driver = _failure_driver_module()
    monkeypatch.setattr(
        driver, "receiver_recovery", lambda run: {"handler": "receiver-recovery", "run": run}
    )
    monkeypatch.setattr(sys, "argv", ["driver", "--scenario", "receiver-recovery", "--run", "r1"])

    assert driver.main() == 0
    _assert_driver_dispatch(capsys, "receiver-recovery")


def test_cancel_before_dispatch_uses_locked_database_boundary_and_public_cancel_only():
    driver = (ROOT / "tests/acceptance/non_bypass_failure_driver.py").read_text(encoding="utf-8")

    assert all(
        value in driver
        for value in (
            "def cancel_before_dispatch(",
            "_arm_cancel_before_dispatch_gate",
            "cancel-before-dispatch-held",
            'state="reserved", outcome=None',
            'state="cancelled", outcome="unknown"',
            '"attemptCount": 0',
            '"reconciliation": "unknown"',
            "control_final_list",
            "control_final_read",
            "primary_result",
        )
    )
    cancel_section = driver.split("def cancel_before_dispatch(", 1)[1].split(
        "def duplicate_dlq(", 1
    )[0]
    assert "_before_send_start" not in cancel_section
    assert "proof-admin-pg-relay" in driver


def test_cancel_before_dispatch_overlay_relays_primary_database_only():
    overlay = yaml.load(
        (ROOT / "docker-compose.non-bypass-failure.yml").read_text(encoding="utf-8"),
        Loader=ComposeLoader,
    )
    services = overlay["services"]
    relay = services["proof-admin-pg-relay"]

    assert services["proof-admin"]["environment"]["DATABASE_URL"] == (
        "postgresql+asyncpg://proof:proof@proof-admin-pg-relay:5432/proof_admin"
    )
    assert services["proof-admin-control"]["environment"]["DATABASE_URL"] == (
        "postgresql+asyncpg://proof:proof@proof-admin-postgres:5432/proof_admin"
    )
    assert relay["environment"]["TARGET_HOST"] == "proof-admin-postgres"
    assert relay["networks"] == ["proof-control", "proof-fault"]
    assert relay["volumes"] == [
        "${PROOF_ARTIFACT_DIR:-./.artifacts/non-bypass-failures}:/proof-artifacts"
    ]


def test_control_admin_can_reconcile_with_the_configured_receiver_authority():
    overlay = yaml.load(
        (ROOT / "docker-compose.non-bypass-failure.yml").read_text(encoding="utf-8"),
        Loader=ComposeLoader,
    )

    expected = {
        "SSL_CERT_FILE": "/run/proof/ca.pem",
        "CONTROLLED_RECEIVER_REGISTRY_JSON": "${CONTROLLED_RECEIVER_REGISTRY_JSON:-{}}",
        "CONTROLLED_RECEIVER_CREDENTIALS_JSON": "${CONTROLLED_RECEIVER_CREDENTIALS_JSON:-{}}",
        "CONTROLLED_RECEIVER_RECEIPT_KEYS_JSON": "${CONTROLLED_RECEIVER_RECEIPT_KEYS_JSON:-{}}",
    }
    assert expected.items() <= overlay["services"]["proof-admin-control"]["environment"].items()


def test_cancellation_runners_allow_long_public_driver():
    runner = (ROOT / "scripts/run_non_bypass_failure_matrix.py").read_text(encoding="utf-8")
    section = runner.split("def _facts_from_driver(", 1)[1].split(
        'if ledger.scenario == "signed-zero"', 1
    )[0]

    assert '"cancel-before-dispatch"' in section
    assert '"cancel-in-flight"' in section
    assert "process.communicate(timeout=360)" in section


def test_failure_driver_dispatches_cancel_before_dispatch(monkeypatch, capsys):
    driver = _failure_driver_module()
    monkeypatch.setattr(
        driver,
        "cancel_before_dispatch",
        lambda run: {"handler": "cancel-before-dispatch", "run": run},
    )
    monkeypatch.setattr(
        sys, "argv", ["driver", "--scenario", "cancel-before-dispatch", "--run", "r1"]
    )

    assert driver.main() == 0
    _assert_driver_dispatch(capsys, "cancel-before-dispatch")


def test_cancel_in_flight_uses_two_public_operations_and_signed_reconciliation_only():
    driver = (ROOT / "tests/acceptance/non_bypass_failure_driver.py").read_text(encoding="utf-8")
    proxy = (ROOT / "tests/acceptance/fault_tools/proof_delivery_proxy.py").read_text(
        encoding="utf-8"
    )

    assert all(
        value in proxy
        for value in (
            "HOLD_VALID_RESPONSE",
            "RELEASE_VALID_RESPONSE",
            "DROP_VALID_RESPONSE",
            '"/_gate/delivery/status"',
            '"responseHeld"',
            "verify_receipt",
        )
    )
    assert all(
        value in driver
        for value in (
            "def cancel_in_flight(",
            '"hold_valid_response"',
            '"release_valid_response"',
            '"drop_valid_response"',
            '"pass_through"',
            "release_cancel",
            "drop_cancel",
            "release_final",
            "drop_final",
            "drop_reconciliation",
            "responseHeld",
            '"attemptCount": 1',
            '"reconciliation": f"signed_{drop_outcome}"',
        )
    )
    section = driver.split("def cancel_in_flight(", 1)[1].split("def duplicate_dlq(", 1)[0]
    assert "responseHeld" not in section.split("return _failure_result(", 1)[1]
    assert "lateEffectAbsenceClaim" not in section


def test_failure_driver_dispatches_cancel_in_flight(monkeypatch, capsys):
    driver = _failure_driver_module()
    monkeypatch.setattr(
        driver,
        "cancel_in_flight",
        lambda run: {"handler": "cancel-in-flight", "run": run},
    )
    monkeypatch.setattr(sys, "argv", ["driver", "--scenario", "cancel-in-flight", "--run", "r1"])

    assert driver.main() == 0
    _assert_driver_dispatch(capsys, "cancel-in-flight")


def test_runner_removes_only_unused_networks_from_its_own_failure_project(monkeypatch, tmp_path):
    runner = _runner_module()
    calls: list[list[str]] = []

    class Result:
        def __init__(self, stdout: str = "", returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    def docker(command, **_kwargs):
        calls.append(command)
        if command[:3] == ["docker", "network", "ls"]:
            assert command[-1] == "label=com.docker.compose.project=nbf-own-run"
            return Result("unused\nin-use\n")
        if command[:3] == ["docker", "network", "inspect"]:
            return Result("0\n" if command[3] == "unused" else "2\n")
        if command[:3] == ["docker", "network", "rm"]:
            assert command[3] == "unused"
            return Result()
        raise AssertionError(command)

    monkeypatch.setattr(runner.subprocess, "run", docker)
    ledger = runner.ScenarioLedger("cancel-in-flight", "nbf-own-run", tmp_path, tmp_path)

    assert runner._remove_unused_project_networks(ledger, {}, deadline=time.monotonic() + 10) == [
        "unused"
    ]
    assert ["docker", "network", "rm", "in-use"] not in calls


def test_runner_never_removes_non_failure_project_networks(monkeypatch, tmp_path):
    runner = _runner_module()
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Docker must not be called"),
    )
    ledger = runner.ScenarioLedger("cancel-in-flight", "geo-xi", tmp_path, tmp_path)

    assert runner._remove_unused_project_networks(ledger, {}, deadline=time.monotonic() + 10) == []


def _runner_module():
    path = ROOT / "scripts/run_non_bypass_failure_matrix.py"
    spec = importlib.util.spec_from_file_location("failure_runner_catalog", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _failure_driver_module():
    path = ROOT / "tests/acceptance/non_bypass_failure_driver.py"
    spec = importlib.util.spec_from_file_location("non_bypass_failure_driver", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_storage_loss_source_binding_id_fits_public_api_bound():
    driver = _failure_driver_module()
    source_id = driver._storage_loss_source_id("run-" + "x" * 128, 3, "store-redis-committed-xadd")
    assert len(source_id) <= 33
    assert len(f"{source_id}-v1") <= 36


def test_catalog_build_occurs_once_before_multiple_fresh_rows(monkeypatch, tmp_path):
    runner = _runner_module()
    calls: list[str] = []
    monkeypatch.setattr(runner, "SCENARIO_ORDER", ("first", "second"))
    monkeypatch.setattr(
        runner,
        "_build_catalog",
        lambda *_args: (
            calls.append("build")
            or {"digest": "catalog", "fixtureDigest": "fixture", "imageIds": {}}
        ),
    )
    monkeypatch.setattr(runner, "_make_secrets", lambda _ledger: {})
    monkeypatch.setattr(runner, "_make_identities", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_configure_failure_receiver", lambda *_args: None)
    monkeypatch.setattr(runner, "_fixture_digest", lambda: "fixture")
    monkeypatch.setattr(
        runner, "_admit", lambda ledger, *_args: calls.append(f"admit:{ledger.scenario}") or {}
    )
    monkeypatch.setattr(runner, "_compose", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        runner, "_facts_from_driver", lambda ledger, *_args: {"run": ledger.project}
    )
    monkeypatch.setattr(runner, "_govern", lambda *_args, **_kwargs: ({}, b"{}", {}, []))
    monkeypatch.setattr(runner, "verify_public_artifacts", lambda *_args: None)
    monkeypatch.setattr(runner, "_cleanup", lambda *_args: None)
    runner.run_matrix(
        tmp_path,
        compose_file=ROOT / "docker-compose.non-bypass-acceptance.yml",
        overlay_file=ROOT / "docker-compose.non-bypass-failure.yml",
    )
    assert calls == ["build", "admit:first", "admit:second"]


def test_catalog_uses_buildx_loaded_images_not_legacy_builder():
    runner = _runner_module()
    commands = runner._catalog_build_commands(
        {name: f"opencli-proof-{name}:catalog" for name in runner.CATALOG_NAMES},
        root_cache_bust="catalog",
    )
    assert len(commands) == 6
    assert all(command[:4] == ["docker", "buildx", "build", "--load"] for _, command in commands)
    root_command = dict(commands)["root"]
    assert ("--build-arg", "PROOF_CATALOG_DIGEST=catalog") in zip(root_command, root_command[1:])


def test_per_row_admission_only_configs_and_inspects_prebuilt_catalog(monkeypatch, tmp_path):
    runner = _runner_module()
    ledger = runner.ScenarioLedger("first", "project", tmp_path, tmp_path)
    compose_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        runner,
        "_compose",
        lambda _ledger, _env, _base, _overlay, *args, **_kwargs: (
            compose_calls.append(args) or "pinned\nroot"
        ),
    )
    monkeypatch.setattr(
        runner,
        "_inspect_images",
        lambda *_args, **_kwargs: {"pinned": "sha256:pinned", "root": "sha256:root"},
    )
    monkeypatch.setattr(runner, "_fixture_digest", lambda: "fixture")
    monkeypatch.setattr(runner, "PINNED_III", "pinned")
    runner._admit(
        ledger,
        {},
        ROOT / "docker-compose.non-bypass-acceptance.yml",
        ROOT / "docker-compose.non-bypass-failure.yml",
        {
            "fixtureDigest": "fixture",
            "digest": "catalog",
            "imageIds": {"pinned": "sha256:pinned", "root": "sha256:root"},
        },
    )
    assert compose_calls == [("config", "--quiet"), ("config", "--images")]
