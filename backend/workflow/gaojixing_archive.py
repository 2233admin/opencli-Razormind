"""Atomic writer for the canonical Gaojixing 2.2 project archive."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PRE_CLEANUP_RECEIPT_SCHEMA = "gaojixing.pre-cleanup-receipt.v1"


def write_precleanup_capture_receipt(
    storage_root: Path,
    *,
    run_id: str,
    workflow_id: str | None,
    question: str,
    package_digest: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Durably spool one capture before its remote conversation is deleted.

    The returned receipt is safe to attach to workflow events and records: it
    contains a path relative to the configured Gaojixing storage root, never a
    host path. Identical evidence for the same question is idempotent, while a
    materially different retry receives a distinct file.
    """

    normalized_run_id = run_id.strip()
    if not normalized_run_id:
        raise ValueError("precleanup_receipt_run_id_required")
    if not question.strip():
        raise ValueError("precleanup_receipt_question_required")
    normalized_digest = package_digest.strip().lower()
    if len(normalized_digest) != 64 or any(
        character not in "0123456789abcdef" for character in normalized_digest
    ):
        raise ValueError("precleanup_receipt_package_digest_invalid")
    if not isinstance(evidence, dict):
        raise ValueError("precleanup_receipt_evidence_invalid")

    evidence_bytes = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    evidence_digest = hashlib.sha256(evidence_bytes).hexdigest()
    run_key = hashlib.sha256(normalized_run_id.encode("utf-8")).hexdigest()
    relative_path = (
        Path("pre-cleanup-evidence")
        / run_key[:20]
        / f"{normalized_digest[:20]}-{evidence_digest[:20]}.json"
    )
    document = {
        "schema": PRE_CLEANUP_RECEIPT_SCHEMA,
        "recorded_at": datetime.now(UTC).isoformat(),
        "run_id": normalized_run_id,
        "workflow_id": workflow_id,
        "question": question,
        "package_digest": normalized_digest,
        "evidence_digest": evidence_digest,
        "evidence": deepcopy(evidence),
    }
    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    root = storage_root.resolve()
    persisted_payload = _atomic_create_or_read(root / relative_path, payload)
    _validate_precleanup_receipt_payload(
        persisted_payload,
        run_id=normalized_run_id,
        workflow_id=workflow_id,
        question=question,
        package_digest=normalized_digest,
        evidence_digest=evidence_digest,
        evidence=evidence,
    )
    return {
        "schema": PRE_CLEANUP_RECEIPT_SCHEMA,
        "persisted": True,
        "path": relative_path.as_posix(),
        "sha256": hashlib.sha256(persisted_payload).hexdigest(),
        "evidence_sha256": evidence_digest,
    }


def write_question_capture(project_root: Path, capture: dict[str, Any]) -> str:
    question_id = str(capture.get("id") or "")
    if not question_id or any(character not in "GB0123456789" for character in question_id):
        raise ValueError("invalid_question_id")
    payload = json.dumps(
        capture,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    _atomic_write(project_root / "raw" / f"{question_id}.json", payload)
    return hashlib.sha256(payload).hexdigest()


def read_question_capture(project_root: Path, question_id: str) -> dict[str, Any] | None:
    path = project_root / "raw" / f"{question_id}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def promote_capture_artifacts(
    staging_root: Path,
    project_root: Path,
    capture: dict[str, Any],
) -> dict[str, Any]:
    """Publish one winning attempt's screenshots and rewrite every artifact reference.

    Drivers only receive ``staging_root``.  Nothing under that attempt directory is
    canonical until the worker has re-validated its database fence and calls this
    function while holding that fence.
    """

    promoted = deepcopy(capture)
    question_id = str(promoted.get("id") or "")
    page_evidence = promoted.get("page_evidence")
    screenshot_files = (
        page_evidence.get("screenshot_files")
        if isinstance(page_evidence, dict)
        else None
    )
    if not isinstance(screenshot_files, list):
        raise ValueError("screenshots_missing")
    page_evidence["screenshot_files"] = [
        _promote_attempt_artifact(
            staging_root,
            project_root,
            artifact,
            question_id=question_id,
        )
        for artifact in screenshot_files
    ]

    page_modules = promoted.get("page_modules")
    videos = page_modules.get("video_links") if isinstance(page_modules, dict) else None
    if isinstance(videos, list):
        for video in videos:
            if isinstance(video, dict) and isinstance(video.get("screenshot_file"), str):
                video["screenshot_file"] = _promote_attempt_artifact(
                    staging_root,
                    project_root,
                    video["screenshot_file"],
                    question_id=question_id,
                )
    return promoted


def promote_verification_artifact(
    staging_root: Path,
    project_root: Path,
    capture: dict[str, Any],
) -> str:
    verification = capture.get("verification")
    if not isinstance(verification, dict) or verification.get("kind") not in {
        "captcha",
        "login",
        "access",
    }:
        raise ValueError("verification_kind_invalid")
    supplied = verification.get("screenshotPath")
    if not isinstance(supplied, str) or not supplied:
        raise ValueError("verification_evidence_missing")
    relative = _promote_attempt_artifact(
        staging_root,
        project_root,
        supplied,
        question_id=str(capture.get("id") or "verification"),
        category="verification",
    )
    return f"run-artifact:{relative}"


def _promote_attempt_artifact(
    staging_root: Path,
    project_root: Path,
    artifact: Any,
    *,
    question_id: str,
    category: str = "screenshots",
) -> str:
    if not isinstance(artifact, str) or not artifact:
        raise ValueError("artifact_reference_invalid")
    staging = staging_root.resolve()
    supplied = Path(artifact)
    candidates = (
        [supplied]
        if supplied.is_absolute()
        else [staging / supplied, staging / "screenshots" / supplied]
    )
    source: Path | None = None
    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(staging)
        except ValueError:
            continue
        if resolved.is_file():
            source = resolved
            break
    if source is None:
        raise ValueError("attempt_artifact_missing")

    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    safe_question_id = "".join(
        character for character in question_id if character.isalnum() or character in "-_"
    ) or "unknown"
    destination_relative = (
        Path(category)
        / safe_question_id
        / f"{digest[:20]}_{source.name}"
    )
    destination = project_root / destination_relative
    if destination.exists():
        current_digest = (
            hashlib.sha256(destination.read_bytes()).hexdigest()
            if destination.is_file()
            else None
        )
        if current_digest != digest:
            raise ValueError("canonical_artifact_digest_conflict")
    else:
        _atomic_write(destination, payload)
    return destination_relative.as_posix()


def finalize_archive(project_root: Path, captures: list[dict[str, Any]]) -> None:
    phase1 = [capture for capture in captures if str(capture.get("id") or "").startswith("G")]
    phase2 = [capture for capture in captures if str(capture.get("id") or "").startswith("B")]
    _atomic_write(
        project_root / "阶段1_非品牌问句归档.md",
        "\n".join(_markdown_entry(capture).rstrip() for capture in phase1).encode("utf-8"),
    )
    _atomic_write(
        project_root / "阶段2_品牌问句归档.md",
        "\n".join(_markdown_entry(capture).rstrip() for capture in phase2).encode("utf-8"),
    )
    total = len(captures)
    status = {
        "completed_count": total,
        "phase1_complete": True,
        "phase2_complete": True,
        "final_summary": {
            "status": "ALL COMPLETE",
            "total_raw": total,
            "phase1": _phase_summary(len(phase1)),
            "phase2": _phase_summary(len(phase2)),
        },
    }
    _atomic_write(
        project_root / "任务状态.json",
        json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
    )
    progress = (
        f"- 阶段1 已完成 {len(phase1)} / {len(phase1)}\n"
        f"- 阶段2 已完成 {len(phase2)} / {len(phase2)}\n"
    )
    _atomic_write(project_root / "进度日志.md", progress.encode("utf-8"))


def _phase_summary(count: int) -> dict[str, int]:
    return {"total": count, "completed": count, "archive_entries": count}


def _markdown_entry(record: dict[str, Any]) -> str:
    modules = record["page_modules"]
    observation = record["brand_observation"]
    lines = [
        f"## {record['id']}｜{record['question']}",
        "",
        f"- 原问句：{record['question']}",
        "- 状态：已完成",
        f"- 豆包会话 URL（原文）：{record['chat_url']}",
        f"- 采集时间：{record['collected_at']}",
        f"- 回答原文（{len(str(record['answer']))} 字）：",
        "",
        *[f"> {line}" for line in str(record["answer"]).splitlines()],
        "",
    ]
    for key, label in (
        ("keywords", "页面显示的关键词"),
        ("ref_links", "参考资料"),
        ("product_links", "产品外链"),
        ("video_links", "相关视频"),
        ("followups", "推荐追问"),
    ):
        value = modules[key]
        if value == "页面未显示":
            lines.append(f"- {label}：页面未显示")
            continue
        lines.append(f"- {label}（{len(value)} 项，按页面顺序）：")
        lines.extend(
            f"  {index}. {_render_module_item(item)}"
            for index, item in enumerate(value, start=1)
        )
    positions = observation["positions"]
    lines.extend(
        [
            f"- 高吉星是否出现：{'是' if observation['appeared'] else '否'}",
            "- 高吉星出现位置："
            + (
                "页面未出现"
                if not positions
                else "、".join(_render_module_item(item) for item in positions)
            ),
            "- 自然推荐结论："
            + (
                "不适用（品牌词问句）"
                if observation["natural_recommendation"] is None
                else ("是" if observation["natural_recommendation"] else "否")
            )
            + f"（依据：{observation['basis']}）",
        ]
    )
    lines.extend(
        f"- 原始证据截图：`{filename}`"
        for filename in record["page_evidence"]["screenshot_files"]
    )
    return "\n".join(lines) + "\n"


def _render_module_item(item: Any) -> str:
    if isinstance(item, str):
        return item
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_create_or_read(path: Path, payload: bytes) -> bytes:
    """Create an immutable file once, or return the winning concurrent payload."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            existing = path.read_bytes()
            _fsync_directory(path.parent)
            return existing
        _fsync_directory(path.parent)
        return payload
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _validate_precleanup_receipt_payload(
    payload: bytes,
    *,
    run_id: str,
    workflow_id: str | None,
    question: str,
    package_digest: str,
    evidence_digest: str,
    evidence: dict[str, Any],
) -> None:
    """Reject a corrupt or conflicting immutable receipt before acknowledging it."""

    try:
        document = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("precleanup_receipt_existing_payload_invalid") from exc
    expected = {
        "schema": PRE_CLEANUP_RECEIPT_SCHEMA,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "question": question,
        "package_digest": package_digest,
        "evidence_digest": evidence_digest,
        "evidence": evidence,
    }
    if not isinstance(document, dict) or any(
        document.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("precleanup_receipt_existing_payload_conflict")


def _fsync_directory(directory: Path) -> None:
    """Persist a directory entry update where the platform exposes directory fsync."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "PRE_CLEANUP_RECEIPT_SCHEMA",
    "finalize_archive",
    "promote_capture_artifacts",
    "promote_verification_artifact",
    "read_question_capture",
    "write_precleanup_capture_receipt",
    "write_question_capture",
]
