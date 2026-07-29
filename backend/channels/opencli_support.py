"""Small execution helpers shared by the managed OpenCLI channel."""

import csv
import hashlib
import io
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml


def parse_json(raw: str) -> list[dict]:
    json_start = next((i for i, ch in enumerate(raw) if ch in ("{", "[")), None)
    if json_start is None:
        raise ValueError(f"No JSON found in output: {raw[:200]!r}")
    data = json.loads(raw[json_start:])
    return data if isinstance(data, list) else [data]


def parse_yaml(raw: str) -> list[dict]:
    data = yaml.safe_load(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return [{"content": str(data)}]


def parse_csv(raw: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(raw.strip())))


def parse_table(raw: str) -> list[dict]:
    """Parse a cli-table3 Unicode box-drawing table."""
    lines = [line for line in raw.splitlines() if line.strip().startswith("│")]
    if not lines:
        return [{"content": raw}]
    split_row = lambda line: [  # noqa: E731
        cell.strip() for cell in line.strip().strip("│").split("│")
    ]
    headers = split_row(lines[0])
    rows = [
        dict(zip(headers, cells))
        for line in lines[1:]
        if len(cells := split_row(line)) == len(headers)
    ]
    return rows or [{"content": raw}]


def parse_markdown(raw: str) -> list[dict]:
    """Parse a markdown table."""
    lines = [line.strip() for line in raw.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return [{"content": raw}]
    split_row = lambda line: [  # noqa: E731
        cell.strip() for cell in line.strip().strip("|").split("|")
    ]
    headers = split_row(lines[0])
    rows = [
        dict(zip(headers, cells))
        for line in lines[2:]
        if len(cells := split_row(line)) == len(headers)
    ]
    return rows or [{"content": raw}]


@asynccontextmanager
async def browser_endpoint_lease(
    pool: Any,
    endpoint: str | None,
    required_profile_kind: str | None,
    *,
    preacquired: bool,
):
    """Reuse a runner-owned endpoint lease or acquire one for legacy callers."""
    if preacquired:
        if not endpoint:
            raise ValueError("A pre-acquired browser lease requires chrome_endpoint")
        yield endpoint
        return

    acquire_kwargs: dict[str, Any] = {"endpoint": endpoint}
    if required_profile_kind:
        acquire_kwargs["required_profile_kind"] = required_profile_kind
    async with pool.acquire(**acquire_kwargs) as leased_endpoint:
        yield leased_endpoint


def extract_opencli_error(stderr_text: str) -> tuple[str | None, str | None]:
    """Read OpenCLI's structured error envelope without depending on its prose."""
    try:
        envelope = yaml.safe_load(stderr_text)
    except yaml.YAMLError:
        envelope = None
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        error = envelope["error"]
        code = str(error.get("code") or "").strip() or None
        message = str(error.get("message") or "").strip() or None
        return code, message

    code_match = re.search(
        r"(?m)^\s+code:\s*['\"]?([A-Za-z0-9_-]+)['\"]?\s*$",
        stderr_text,
    )
    message_match = re.search(r"(?m)^\s+message:\s*(.+?)\s*$", stderr_text)
    code = code_match.group(1) if code_match else None
    message = message_match.group(1).strip(" '\"") if message_match else None
    return code, message


def artifact_sha256(artifact_ref: str) -> str | None:
    """Hash a trace file/directory so its persisted reference is auditable."""
    root = Path(artifact_ref)
    if not root.exists():
        return None
    files = [root] if root.is_file() else sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix(),
    )
    digest = hashlib.sha256()
    base = root.parent if root.is_file() else root
    for path in files:
        digest.update(path.relative_to(base).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()
