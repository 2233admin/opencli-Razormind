#!/usr/bin/env python3
"""Normalize authenticated public-boundary facts into ScenarioResultV1.

This program deliberately accepts no actuator, relay, proxy, database, container,
or page traversal state.  The live runner supplies one public fact document per
scenario after it has exercised the real Compose topology.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from typing import Any

from scripts.non_bypass_failure_proof_contract import SCHEMA_VERSION, content_hash, validate

SCENARIOS = frozenset({
    "admin-crash", "iii-unreachable", "no-report", "signed-zero", "crash-after-ingest",
    "ingest-redis-store-loss", "duplicate-dlq", "query-page-race", "graph-stale-auth-cas-retract",
    "amendment-decision-conflict", "receiver-recovery", "cancel-before-dispatch", "cancel-in-flight",
})
SCENARIO_ORDER = (
    "admin-crash",
    "iii-unreachable",
    "no-report",
    "signed-zero",
    "crash-after-ingest",
    "ingest-redis-store-loss",
    "duplicate-dlq",
    "query-page-race",
    "graph-stale-auth-cas-retract",
    "amendment-decision-conflict",
    "receiver-recovery",
    "cancel-before-dispatch",
    "cancel-in-flight",
)
PUBLIC_FACT_KEYS = frozenset({
    "scenario", "run", "fault", "actuator", "correlation", "collection", "materialization", "graph", "delivery",
    "redactionProfile", "timing", "governanceReference", "authority",
})


class PublicFactRejected(RuntimeError):
    pass


def normalize_public_facts(facts: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(facts, dict) or set(facts) != PUBLIC_FACT_KEYS:
        actual = sorted(facts) if isinstance(facts, dict) else type(facts).__name__
        raise PublicFactRejected(
            f"public fact document is not the acceptance allowlist: {actual}"
        )
    if facts["authority"] != "authenticated-scoped-public-api":
        raise PublicFactRejected("only authenticated scoped public API facts are admissible")
    if facts["scenario"] not in SCENARIOS:
        raise PublicFactRejected("scenario is not in the failure matrix")
    governance = facts["governanceReference"]
    if not isinstance(governance, dict) or set(governance) != {"artifactId", "keyId", "trustRootFingerprint"}:
        raise PublicFactRejected("governance reference is invalid")
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "scenario": facts["scenario"],
        "run": facts["run"],
        "fault": facts["fault"],
        "actuator": facts["actuator"],
        "correlation": facts["correlation"],
        "collection": facts["collection"],
        "materialization": facts["materialization"],
        "graph": facts["graph"],
        "delivery": facts["delivery"],
        "forbiddenFacts": {
            "adminCreatedFallback": False,
            "lateEffectAbsenceClaim": False,
            "containerAuthority": False,
            "pageFinality": False,
        },
        "redactionProfile": facts["redactionProfile"],
        "timing": facts["timing"],
        "governance": {**governance, "contentHash": ""},
    }
    result["governance"]["contentHash"] = content_hash({key: value for key, value in result.items() if key != "governance"})
    validate(result, scenario=facts["scenario"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    args = parser.parse_args(argv)
    try:
        facts = json.load(sys.stdin)
        if facts.get("scenario") != args.scenario:
            raise PublicFactRejected("selected scenario does not match public facts")
        print(json.dumps(normalize_public_facts(facts), sort_keys=True))
    except (json.JSONDecodeError, PublicFactRejected, RuntimeError) as exc:
        print(f"failure proof rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
