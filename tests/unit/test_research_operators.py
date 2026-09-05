from __future__ import annotations

import copy

import pytest

from backend.workflow.data_operators import execute_data_operator, list_data_operator_specs
from backend.workflow.research_graph_lineage import build_research_operator_lineage_events


def _evidence(
    evidence_id: str | None,
    statement: str,
    *,
    stance: str = "support",
    dimension: str = "facts",
    claim_key: str = "claim-1",
) -> dict[str, object]:
    item: dict[str, object] = {
        "normalizedData": {
            "claimKey": claim_key,
            "statement": statement,
            "stance": stance,
            "dimension": dimension,
        },
        "lineage": [{"nodeId": "normalize"}],
    }
    if evidence_id:
        item["candidateId"] = evidence_id
        item["normalizedData"]["evidenceId"] = evidence_id
        item["normalizedData"]["evidenceRef"] = {
            "evidenceId": evidence_id,
            "itemKey": evidence_id,
            "batchId": "batch-test",
            "runId": "run-test",
            "nodeId": "normalize",
            "manifestUri": f"/api/v1/workflows/runs/run-test/evidence-batches/{evidence_id}",
            "odpRef": f"odp://workflow-runs/run-test/nodes/normalize/batches/{evidence_id}",
        }
    return item


def _run(operator_id: str, items: list[dict], config: dict | None = None):
    return execute_data_operator(operator_id, items, config, pack_version="1.0.0")


def test_research_pack_registers_six_versioned_operators() -> None:
    specs = {
        spec.operator_id: spec
        for spec in list_data_operator_specs()
        if spec.pack_id == "builtin.research"
    }

    assert set(specs) == {
        "research.claim-project",
        "research.coverage-audit",
        "research.counter-thesis",
        "research.scenario-simulate",
        "research.revision-diff",
        "research.publish-gate",
    }
    assert {spec.pack_version for spec in specs.values()} == {"1.0.0"}


def test_claim_project_is_stable_and_preserves_evidence_ids() -> None:
    items = [
        _evidence("evidence-b", "Liquidity remains ample.", dimension="funding"),
        _evidence("evidence-a", "Liquidity remains ample.", stance="contradict", dimension="risk"),
    ]
    original = copy.deepcopy(items)

    first = _run("research.claim-project", items)
    second = _run("research.claim-project", list(reversed(items)))
    claim = first.items[0]["normalizedData"]["claim"]

    assert first.items == second.items
    assert claim["claimId"].startswith("claim-")
    assert claim["supportingEvidenceIds"] == ["evidence-b"]
    assert claim["contradictingEvidenceIds"] == ["evidence-a"]
    assert claim["dimensions"] == ["funding", "risk"]
    assert {reference["evidenceId"] for reference in claim["evidenceRefs"]} == {
        "evidence-a",
        "evidence-b",
    }
    assert claim["disposition"] == "mixed"
    assert items == original


def test_claim_project_marks_uncited_claim_unverified() -> None:
    result = _run("research.claim-project", [_evidence(None, "Uncited statement")])
    claim = result.items[0]["normalizedData"]["claim"]

    assert claim["verificationStatus"] == "unverified"
    assert claim["evidenceIds"] == []
    assert result.metrics["unverifiedClaimCount"] == 1


def test_coverage_audit_finalizes_when_required_dimensions_are_covered() -> None:
    claims = _run(
        "research.claim-project",
        [
            _evidence("capital", "Capital improved.", dimension="funding", claim_key="capital"),
            _evidence("risk", "Risk increased.", dimension="risk", claim_key="risk"),
        ],
    ).items
    result = _run(
        "research.coverage-audit",
        claims,
        {"requiredDimensions": ["funding", "risk"]},
    )
    report = result.items[0]["normalizedData"]["coverageReport"]

    assert report["coveredDimensions"] == ["funding", "risk"]
    assert report["gaps"] == []
    assert report["satisfied"] is True
    assert report["decision"] == "finalize"
    assert report["stopReason"] == "coverage_satisfied"


@pytest.mark.parametrize(
    ("config", "decision", "reason"),
    [
        (
            {
                "requiredDimensions": ["risk"],
                "iteration": 1,
                "maxIterations": 2,
                "additionalCollectionCount": 0,
                "maxAdditionalCollections": 1,
            },
            "collect_more",
            None,
        ),
        (
            {
                "requiredDimensions": ["risk"],
                "iteration": 2,
                "maxIterations": 2,
                "additionalCollectionCount": 0,
                "maxAdditionalCollections": 1,
            },
            "stop_incomplete",
            "max_iterations_reached",
        ),
        (
            {
                "requiredDimensions": ["risk"],
                "iteration": 1,
                "maxIterations": 2,
                "additionalCollectionCount": 1,
                "maxAdditionalCollections": 1,
            },
            "stop_incomplete",
            "max_additional_collections_reached",
        ),
    ],
)
def test_coverage_audit_is_bounded(config: dict, decision: str, reason: str | None) -> None:
    claims = _run("research.claim-project", [_evidence(None, "Unverified")]).items
    result = _run("research.coverage-audit", claims, config)
    report = result.items[0]["normalizedData"]["coverageReport"]

    assert report["decision"] == decision
    assert report["stopReason"] == reason
    assert report["gaps"] == ["risk"]
    if decision == "collect_more":
        assert report["continuationProposal"]["nextIteration"] == 2
        assert report["continuationProposal"]["nextAdditionalCollectionCount"] == 1
    else:
        assert report["continuationProposal"] is None


def test_counter_thesis_keeps_only_counter_evidence() -> None:
    claims = _run(
        "research.claim-project",
        [
            _evidence("support", "Demand will grow."),
            _evidence("oppose", "Demand will grow.", stance="contradict"),
            _evidence("qualify", "Demand will grow.", stance="qualify"),
        ],
    ).items
    result = _run("research.counter-thesis", claims)
    counter = result.items[-1]["normalizedData"]["counterThesis"]

    assert counter["contradictingEvidenceIds"] == ["oppose"]
    assert counter["qualifyingEvidenceIds"] == ["qualify"]
    assert counter["evidenceIds"] == ["oppose", "qualify"]
    assert "support" not in counter["evidenceIds"]


def test_scenario_simulation_scores_configured_drivers_with_evidence_refs() -> None:
    claims = _run(
        "research.claim-project",
        [_evidence("support", "Demand will grow.", dimension="demand")],
    ).items
    result = _run(
        "research.scenario-simulate",
        claims,
        {
            "scenarios": [
                {
                    "scenarioId": "upside",
                    "label": "Upside",
                    "priorScore": 0.4,
                    "drivers": [{"dimension": "demand", "weight": 0.3}],
                    "assumptions": ["Demand evidence remains current."],
                    "invalidationSignals": ["Demand evidence reverses."],
                }
            ]
        },
    )
    scenario = result.items[-1]["normalizedData"]["researchScenario"]

    assert scenario["score"] == pytest.approx(0.7)
    assert scenario["uncoveredDrivers"] == []
    assert scenario["evidenceIds"] == ["support"]
    assert scenario["evidenceRefs"][0]["nodeId"] == "normalize"
    assert result.metrics["scenarioCount"] == 1


def test_revision_diff_reports_added_changed_and_removed_claims() -> None:
    claims = _run(
        "research.claim-project",
        [
            _evidence("new-evidence", "New claim.", claim_key="new"),
            _evidence("changed-evidence", "Changed claim.", claim_key="changed"),
        ],
    ).items
    changed = next(
        item["normalizedData"]["claim"]
        for item in claims
        if item["normalizedData"]["claim"]["statement"] == "Changed claim."
    )
    previous = [
        {**changed, "statement": "Old statement."},
        {"claimId": "claim-removed", "statement": "Removed", "evidenceIds": ["old"]},
    ]

    result = _run("research.revision-diff", claims, {"previousClaims": previous})
    revision = result.items[-1]["normalizedData"]["researchRevision"]

    assert [item["statement"] for item in revision["added"]] == ["New claim."]
    assert [item["claimId"] for item in revision["changed"]] == [changed["claimId"]]
    assert [item["claimId"] for item in revision["removed"]] == ["claim-removed"]
    assert revision["changed"][0]["before"]["statement"] == "Old statement."
    assert revision["changed"][0]["after"]["statement"] == "Changed claim."


def test_publish_gate_requires_a_complete_evidence_linked_revision() -> None:
    claims = _run(
        "research.claim-project",
        [_evidence("evidence", "Grounded claim.", dimension="facts")],
    ).items
    audited = _run(
        "research.coverage-audit",
        claims,
        {"requiredDimensions": ["facts"]},
    ).items
    revision = _run(
        "research.revision-diff",
        audited,
        {"previousClaims": []},
    ).items
    passed = _run("research.publish-gate", revision)

    assert passed.metrics["publishAllowed"] is True
    assert passed.items == revision
    assert revision[-1]["lineage"] == [{"nodeId": "normalize"}]

    unverified = _run(
        "research.claim-project",
        [_evidence(None, "Unverified claim.")],
    ).items
    audited_unverified = _run(
        "research.coverage-audit",
        unverified,
        {"requiredDimensions": []},
    ).items
    revision_unverified = _run(
        "research.revision-diff",
        audited_unverified,
        {"previousClaims": []},
    ).items
    blocked = _run("research.publish-gate", revision_unverified)

    assert blocked.items == []
    assert blocked.metrics["publishAllowed"] is False
    assert blocked.metrics["gateReasons"] == ["unverified_claims"]
    assert blocked.rejected_count == len(revision_unverified)


def test_publish_gate_rejects_multiple_refs_for_one_evidence_id() -> None:
    first = _evidence("shared", "Grounded claim.", dimension="facts")
    second = copy.deepcopy(first)
    second["candidateId"] = "second-item"
    reference = second["normalizedData"]["evidenceRef"]
    reference["itemKey"] = "second-item"
    reference["batchId"] = "second-batch"
    reference["manifestUri"] = "/evidence-batches/second-batch"
    claims = _run(
        "research.claim-project",
        [first, second],
    ).items
    claim = claims[0]["normalizedData"]["claim"]
    assert len(claim["evidenceRefs"]) == 2

    audited = _run(
        "research.coverage-audit",
        claims,
        {"requiredDimensions": ["facts"]},
    ).items
    revision = _run(
        "research.revision-diff",
        audited,
        {"previousClaims": []},
    ).items
    blocked = _run("research.publish-gate", revision)
    assert blocked.metrics["publishAllowed"] is False
    assert blocked.metrics["gateReasons"] == ["unverified_claims"]

def test_publish_gate_rejects_stale_coverage_and_revision_artifacts() -> None:
    claims = _run(
        "research.claim-project",
        [_evidence("evidence", "Grounded claim.", dimension="facts")],
    ).items
    audited = _run(
        "research.coverage-audit",
        claims,
        {"requiredDimensions": ["facts"]},
    ).items
    revision = _run(
        "research.revision-diff",
        audited,
        {"previousClaims": []},
    ).items

    stale_coverage = copy.deepcopy(revision)
    for item in stale_coverage:
        normalized = item.get("normalizedData", {})
        report = normalized.get("coverageReport")
        if isinstance(report, dict):
            report["claimSetHash"] = "stale"
    blocked_coverage = _run("research.publish-gate", stale_coverage)
    assert blocked_coverage.metrics["gateReasons"] == ["coverage_not_satisfied"]

    stale_revision = copy.deepcopy(revision)
    stale_revision[-1]["normalizedData"]["researchRevision"][
        "claimSetHash"
    ] = "stale"
    blocked_revision = _run("research.publish-gate", stale_revision)
    assert blocked_revision.metrics["gateReasons"] == ["missing_revision"]


def test_research_operator_unknown_config_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported config"):
        _run("research.claim-project", [], {"invented": True})


def test_automatic_graph_lineage_requires_complete_actual_provenance() -> None:
    complete = {
        "normalizedData": {
            "claim": {
                "claimId": "claim-1",
                "statement": "Evidence-grounded claim.",
                "evidenceRefs": [
                    {
                        "sourceId": "source-1",
                        "evidenceId": "evidence-1",
                        "itemKey": "item-1",
                        "batchId": "batch-1",
                        "runId": "run-1",
                        "nodeId": "normalize-1",
                        "manifestUri": "odp://manifest-1",
                    }
                ],
            }
        }
    }
    missing_source = copy.deepcopy(complete)
    del missing_source["normalizedData"]["claim"]["evidenceRefs"][0]["sourceId"]

    events = build_research_operator_lineage_events(
        [complete, missing_source],
        run_id="run-1",
        workflow_id="workflow-1",
        trace_id="trace-1",
        node_id="claim-project",
        sequence=3,
    )

    assert [
        event.details["researchGraph"]["entity"]["kind"] for event in events
    ] == ["source", "evidence", "claim"]
    assert {event.details["researchGraph"]["entity"]["id"] for event in events} == {
        "source:source-1",
        "evidence:evidence-1",
        "claim:claim-1",
    }
