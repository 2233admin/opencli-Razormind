import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.workflow.gaojixing_archive import (
    PRE_CLEANUP_RECEIPT_SCHEMA,
    write_precleanup_capture_receipt,
)


def test_write_precleanup_capture_receipt_is_durable_and_path_safe(tmp_path):
    evidence = {
        "kind": "doubao.capture.pre_cleanup",
        "response": {
            "answer": "完整回答",
            "links": [{"url": "https://example.test/product"}],
            "suggested_keywords": ["推荐追问"],
            "conversation_deleted": False,
        },
    }
    package_digest = hashlib.sha256(b"question-package").hexdigest()

    receipt = write_precleanup_capture_receipt(
        tmp_path,
        run_id="run/with/path-like-input",
        workflow_id="workflow-1",
        question="测试问题",
        package_digest=package_digest,
        evidence=evidence,
    )

    assert receipt["schema"] == PRE_CLEANUP_RECEIPT_SCHEMA
    assert receipt["persisted"] is True
    path = (tmp_path / receipt["path"]).resolve()
    path.relative_to(tmp_path.resolve())
    assert "run/with/path-like-input" not in receipt["path"]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["run_id"] == "run/with/path-like-input"
    assert document["question"] == "测试问题"
    assert document["evidence"] == evidence
    assert document["evidence_digest"] == receipt["evidence_sha256"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["sha256"]


def test_write_precleanup_capture_receipt_is_immutable_across_concurrent_replays(tmp_path):
    evidence = {
        "kind": "doubao.capture.pre_cleanup",
        "response": {"answer": "完整回答", "conversation_deleted": False},
    }
    package_digest = hashlib.sha256(b"question-package").hexdigest()

    def write_once(_index):
        return write_precleanup_capture_receipt(
            tmp_path,
            run_id="run-1",
            workflow_id="workflow-1",
            question="测试问题",
            package_digest=package_digest,
            evidence=evidence,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(write_once, range(16)))

    assert {receipt["path"] for receipt in receipts} == {receipts[0]["path"]}
    assert {receipt["sha256"] for receipt in receipts} == {receipts[0]["sha256"]}
    persisted = tmp_path / receipts[0]["path"]
    assert hashlib.sha256(persisted.read_bytes()).hexdigest() == receipts[0]["sha256"]


@pytest.mark.parametrize(
    ("run_id", "package_digest", "expected"),
    [
        ("", "0" * 64, "run_id_required"),
        ("run", "../unsafe", "package_digest_invalid"),
    ],
)
def test_write_precleanup_capture_receipt_rejects_invalid_identity(
    tmp_path,
    run_id,
    package_digest,
    expected,
):
    with pytest.raises(ValueError, match=expected):
        write_precleanup_capture_receipt(
            tmp_path,
            run_id=run_id,
            workflow_id="workflow-1",
            question="测试问题",
            package_digest=package_digest,
            evidence={"kind": "doubao.capture.pre_cleanup"},
        )
